"""主流程编排：采集 -> 清洗 -> 入库 -> 分析 -> 推送 -> 记录运行日志。

一个"日报自动化"任务，看起来简单，真正上线要解决的就四件事：
  1. 重复执行不能产生脏数据（幂等）
  2. 任何一步失败都要留下痕迹，能定位（日志 + etl_run）
  3. 失败了不能静默，要告警（推送里有失败信息）
  4. 换数据源不用改主流程（采集器接口）
"""
from __future__ import annotations

import time
import traceback
import uuid
from datetime import date, timedelta
# 打印更美观,方便调试时查看，不要删掉
from rich import print as rprint

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .analyze import build_alerts, summarize
from .collectors import get_collector
from .config import settings
from .db import engine, init_db, session_scope
from .logger import get_logger
from .models import DailySkuMetric, EtlRun
from .notify import render_report, save_report, send_feishu, PushResult, decide_push
from .transform import transform

log = get_logger(__name__)

SHOP_ID = "DY001"  # 店铺编号；多店场景下作为分区键

_UPSERT_COLS = [
    "sku_name", "category", "qty_sold", "return_qty", "stock_qty", "gmv",
    "refund_amount", "commission", "ad_cost", "shipping_cost", "cost_of_goods",
    "gross_profit", "gross_margin", "return_rate", "stock_days", "sell_through_rate",
]
_PK = ["biz_date", "shop_id", "sku_id"]


def _save(df: pd.DataFrame, biz_date: date, shop_id: str) -> int:
    """幂等写入：一条 INSERT ... ON CONFLICT DO UPDATE，不再逐行 SELECT+UPDATE。"""
    payload = [
        {"biz_date": biz_date, "shop_id": shop_id, "sku_id": r["sku_id"],
         **{c: r[c] for c in _UPSERT_COLS}}
        for r in df.to_dict("records")
    ]

    dialect = engine.dialect.name
    if dialect not in ("postgresql", "sqlite"):
        # 其他方言退回 merge，保证能跑（MySQL 用 ON DUPLICATE KEY UPDATE，需另写）
        with session_scope() as s:
            for row in payload:
                s.merge(DailySkuMetric(**row))
        return len(payload)

    ins = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = ins(DailySkuMetric).values(payload)

    stmt = stmt.on_conflict_do_update(
        index_elements=_PK,                              # 冲突判定依据，必须是主键或唯一约束
        set_={c: stmt.excluded[c] for c in _UPSERT_COLS},  # excluded = 本次想插入的那行
    )
    with session_scope() as s:
        s.execute(stmt)
    return len(payload)


def _load_day(biz_date: date, shop_id: str) -> pd.DataFrame:
    """从库里读某一天的明细（用于环比）。"""
    with session_scope() as session:
        rows = (
            session.query(DailySkuMetric)
            .filter(DailySkuMetric.biz_date == biz_date, DailySkuMetric.shop_id == shop_id)
            .all()
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "sku_id": r.sku_id, "sku_name": r.sku_name, "category": r.category,
        "qty_sold": r.qty_sold, "gmv": float(r.gmv), "gross_profit": float(r.gross_profit),
    } for r in rows])


def _write_run(run_id: str, biz_date: date, source: str, status: str,
               rows: int, cost_ms: int, message: str | None = None,
               push_status: str | None = None) -> None:
    with session_scope() as session:
        session.merge(EtlRun(
            run_id=run_id, biz_date=biz_date, source=source, status=status,
            rows=rows, cost_ms=cost_ms, message=(message or "")[:2000] or None,
            push_status=push_status,
        ))


def run(biz_date: date | None = None, source: str | None = None,
        dry_run: bool = False, shop_id: str = SHOP_ID) -> dict:
    """执行一次完整日报任务，返回结果字典。

    dry_run 语义：零业务副作用——不写明细表、不写审计表、不落文件、不发消息；
    但允许 init_db() 确保表结构存在，否则读昨日数据会报表不存在。
    """
    biz_date = biz_date or (date.today() - timedelta(days=1))
    source = source or settings.source
    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    log.info("=" * 60)
    log.info("任务开始 run_id=%s 日期=%s 数据源=%s dry_run=%s", run_id, biz_date, source, dry_run)

    init_db()
    rows_planned = 0  # 处理了多少行（dry_run 时也有效）
    rows_saved = 0    # 实际写入多少行（dry_run 恒为 0）

    try:
        # 采集数据
        raw = get_collector(source).fetch(biz_date)
        # 清洗数据
        df = transform(raw)

        rows_planned = len(df)

        # 写入数据库，负责幂等；dry_run 只预览不落库
        if dry_run:
            log.info("dry_run 模式：跳过入库，%d 行未写入", rows_planned)
        else:
            rows_saved = _save(df, biz_date, shop_id)

        # 环比：从 数据库 读昨天数据，不依赖内存；`prev_gmv` 为 0 时跳过
        prev = _load_day(biz_date - timedelta(days=1), shop_id)

        # 分析：生成汇总并跑 5 条预警规则
        summary = summarize(df, biz_date.isoformat())
        if not prev.empty:
            prev_gmv = float(prev["gmv"].sum())
            if prev_gmv:
                summary.gmv_dod = round((summary.gmv - prev_gmv) / prev_gmv, 4)

        alerts = build_alerts(df, prev)

        summary.alerts = alerts

        # 推送：渲染；`dry_run` 只打印，否则写入文件并推送
        # 存档留全量预警（证据链完整），推送用截断版（手机上超过一屏没人看）
        text = render_report(summary, alerts, max_alerts=None)
        push: PushResult | None = None
        if dry_run:
            print("\n" + text + "\n")
            log.info("dry_run 模式：不写库、不落盘、不推送")
        else:
            save_report(text, biz_date.isoformat())
            go, reason = decide_push(alerts, settings.push_policy)
            if go:
                push = send_feishu(render_report(summary, alerts))
            else:
                push = PushResult.SKIPPED_BY_POLICY
                log.info("按策略 %s 跳过推送（%s），报告已落盘", settings.push_policy, reason)

        cost_ms = int((time.perf_counter() - t0) * 1000)
        # 记录日志：dry_run 不是真实执行，不写 etl_run 审计表
        if not dry_run:
            # status 只表达 ETL 结果，推送结果进独立字段，互不污染
            _write_run(run_id, biz_date, source, "success", rows_saved, cost_ms,
                       push_status=push.value if push else None)

        log.info("任务成功: 处理 %d 行 / 写入 %d 行, 耗时 %dms", rows_planned, rows_saved, cost_ms)

        return {
            "ok": True, "run_id": run_id, "biz_date": biz_date.isoformat(),
            "dry_run": dry_run, "rows": rows_planned, "rows_saved": rows_saved,
            "summary": summary, "alerts": alerts, "cost_ms": cost_ms,
        }

    except Exception as exc:  # noqa: BLE001
        cost_ms = int((time.perf_counter() - t0) * 1000)
        err = f"{type(exc).__name__}: {exc}"
        log.error("任务失败: %s", err)
        log.debug(traceback.format_exc())
        # dry_run 是预演，失败同样不写审计表、不惊动业务群
        if not dry_run:
            # 失败告警的推送结果不写 push_status：该字段只表示"日报推送"，
            # 混入告警推送会让"失败但告警成功"看起来像推送正常。
            _write_run(run_id, biz_date, source, "failed", rows_saved, cost_ms, err)
            # 失败也要让业务方知道，否则他以为只是今天没数据
            send_feishu(f"【自动化任务失败】{biz_date} 数据源 {source}\n原因: {err}\n请检查后手动重跑")
        return {"ok": False, "run_id": run_id, "error": err, "cost_ms": cost_ms}
