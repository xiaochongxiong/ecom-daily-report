"""报表渲染与通知推送。

要点：推送失败不能让主流程崩掉（数据已经入库了，报告没发出去是次要问题，
但要记日志、要能补发）。这是"生产级"和"玩具项目"的区别。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import requests

from .analyze import Alert, Summary
from .config import settings
from .logger import get_logger

log = get_logger(__name__)

_LEVEL_TAG = {"high": "[高]", "mid": "[中]", "low": "[低]"}

_MAX_ATTEMPTS = 3            # 首次 + 2 次重试
_RETRY_DELAYS = (1, 3)       # 第 2 次前等 1s，第 3 次前等 3s（指数退避）



class PushResult(StrEnum):
    """推送终态，值直接落库到 etl_run.push_status。

    跳过必须再细分成两种，否则运维会把"业务主动不推"误判成"配置坏了"：
      skipped_no_webhook —— 没配 webhook，是故障，要修
      skipped_by_policy  —— 按 PUSH_POLICY 主动不推，是正常业务行为
    """
    OK = "ok"
    SKIPPED_NO_WEBHOOK = "skipped_no_webhook"
    SKIPPED_BY_POLICY = "skipped_by_policy"
    FAILED = "failed"


def render_report(s: Summary, alerts: list[Alert], max_alerts: int | None = 12) -> str:
    """把 Summary 渲染成人能读懂的一段文字（飞书/微信/控制台通用）。

    max_alerts=None 表示全量输出 —— 落盘存档用全量，推送用默认 12 条截断：
    存档是证据，一条都不能丢；推送是提醒，手机上超过一屏就没人看。
    """
    lines: list[str] = []
    lines.append(f"电商经营日报 {s.biz_date}")
    lines.append("")
    lines.append(f"成交额 GMV    {s.gmv:>12,.2f}")
    lines.append(f"毛利          {s.gross_profit:>12,.2f}  (毛利率 {s.gross_margin:.1%})")
    if s.gmv_dod is not None:
        arrow = "↑" if s.gmv_dod >= 0 else "↓"
        lines.append(f"GMV 环比      {arrow} {abs(s.gmv_dod):.1%}")
    lines.append(f"推广费        {s.ad_cost:>12,.2f}  (占 GMV {s.ad_ratio:.1%})")
    lines.append(f"退款          {s.refund_amount:>12,.2f}")
    lines.append(f"发货件数      {s.qty_sold:>12,}")
    lines.append(f"库存货值      {s.stock_value:>12,.2f}")
    lines.append(f"动销率        {s.sell_through_rate:>12.1%}")
    lines.append("")

    if s.top_profit:
        lines.append("毛利 Top5")
        for i, (name, v) in enumerate(s.top_profit, 1):
            lines.append(f"  {i}. {name}  {v:,.2f}")
        lines.append("")

    if alerts:
        lines.append(f"异常预警 {len(alerts)} 条（按严重程度排序）")
        shown = alerts if max_alerts is None else alerts[:max_alerts]
        for a in shown:
            lines.append(f"  {_LEVEL_TAG.get(a.level, '')} {a.sku_name}: {a.message}")
        if max_alerts is not None and len(alerts) > max_alerts:
            lines.append(f"  ... 另有 {len(alerts) - max_alerts} 条，完整清单见当日报告文件")
    else:
        lines.append("今日无异常，各项指标正常")

    lines.append("")
    lines.append(f"生成时间 {datetime.now():%Y-%m-%d %H:%M:%S}")
    return "\n".join(lines)


def decide_push(alerts: list[Alert], policy: str = "high_only") -> tuple[bool, str]:
    """决定是否推送。返回 (是否推送, 原因)，纯函数，不碰网络也不碰数据库。

    policy: "always" = 每天必发；"high_only" = 有 high 级预警才发
    原因只用于日志和排查，落库靠 PushResult（skipped_by_policy / skipped_no_webhook 分开记）。
    """
    if policy == "always":
        return True, "always"
    if policy == "high_only":
        has_high = any(a.level == "high" for a in alerts)
        return (True, "has_high_alert") if has_high else (False, "no_high_alert")
    raise ValueError(f"未知推送策略: {policy}")


def send_feishu(text: str, webhook: str | None = None) -> PushResult:
    """推送到飞书群机器人。返回三态结果，永不抛异常。

    重试策略：网络异常、5xx、429 最多重试 2 次（间隔 1s / 3s）；
    4xx 不重试——webhook 失效、签名错这类是配置问题，重试解决不了。
    实测过：容器冷启动首次 POST 会读超时（10s），同一份代码第二次 0.6s 成功，
    所以瞬时抖动必须靠退避重试兜住，否则当天推送直接永久丢失。
    """
    hook = webhook if webhook is not None else settings.feishu_webhook
    if not hook:
        log.info("未配置 FEISHU_WEBHOOK，跳过推送（报告已落盘）")
        return PushResult.SKIPPED_NO_WEBHOOK

    payload = json.dumps(
        {"msg_type": "text", "content": {"text": text}}, ensure_ascii=False
    ).encode("utf-8")

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if attempt > 1:
            delay = _RETRY_DELAYS[attempt - 2]
            log.warning("飞书推送第 %d 次尝试，先等 %s 秒", attempt, delay)
            time.sleep(delay)
        try:
            resp = requests.post(
                hook,
                headers={"Content-Type": "application/json"},
                data=payload,
                timeout=(5, 10),  # (连接, 读取)
            )
        except requests.RequestException as exc:
            log.warning("飞书推送失败（第 %d 次）: %s", attempt, exc)
            continue

        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            log.error("飞书返回非 JSON，放弃: %s", resp.text[:200])
            return PushResult.FAILED

        if resp.status_code == 200 and data.get("code", 0) == 0:
            log.info("飞书推送成功（第 %d 次尝试）", attempt)
            return PushResult.OK
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("飞书返回 %s，可重试: %s", resp.status_code, data)
            continue
        log.error("飞书推送返回异常（不重试）: %s %s", resp.status_code, data)
        return PushResult.FAILED

    log.error("飞书推送最终失败，已尝试 %d 次", _MAX_ATTEMPTS)
    return PushResult.FAILED


def save_report(text: str, biz_date: str) -> Path:
    """落盘一份，方便事后核对或人工补发。"""
    settings.ensure_dirs()
    path = settings.output_dir / f"daily_report_{biz_date}.txt"
    path.write_text(text, encoding="utf-8")
    log.info("报告已保存: %s", path)
    return path
