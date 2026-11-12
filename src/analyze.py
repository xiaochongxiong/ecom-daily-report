"""经营分析与异常预警。

这一步是"从数据到决策"的转折点：
老板不关心你有多少个 SKU，他只关心"今天赚了多少、哪儿在烧钱、明天该干什么"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import settings
from .logger import get_logger

log = get_logger(__name__)


@dataclass
class Alert:
    level: str        # high / mid / low
    alert_type: str   # 预警类型，便于后期做规则统计
    sku_id: str
    sku_name: str
    message: str


@dataclass
class Summary:
    biz_date: str = ""
    sku_count: int = 0
    gmv: float = 0.0
    gross_profit: float = 0.0
    gross_margin: float = 0.0
    refund_amount: float = 0.0
    ad_cost: float = 0.0
    ad_ratio: float = 0.0
    qty_sold: int = 0
    stock_value: float = 0.0
    sell_through_rate: float = 0.0   # 整体动销率 = 总卖出 / 总可供
    gmv_dod: float | None = None      # 环比（日）
    alerts: list[Alert] = field(default_factory=list)
    top_profit: list[tuple[str, float]] = field(default_factory=list)


def summarize(df: pd.DataFrame, biz_date: str) -> Summary:
    gmv = float(df["gmv"].sum())
    gp = float(df["gross_profit"].sum())
    ad = float(df["ad_cost"].sum())

    s = Summary(
        biz_date=biz_date,
        sku_count=int(len(df)),
        gmv=round(gmv, 2),
        gross_profit=round(gp, 2),
        gross_margin=round(gp / gmv, 4) if gmv else 0.0,
        refund_amount=round(float(df["refund_amount"].sum()), 2),
        ad_cost=round(ad, 2),
        ad_ratio=round(ad / gmv, 4) if gmv else 0.0,
        qty_sold=int(df["qty_sold"].sum()),
        stock_value=round(float((df["stock_qty"] * df["unit_cost"]).sum()), 2),
        sell_through_rate=round(
            float(df["qty_sold"].sum()) / float(df["stock_qty"].sum() + df["qty_sold"].sum()), 4
        ) if float(df["stock_qty"].sum() + df["qty_sold"].sum()) else 0.0,
    )

    top = (
        df.sort_values("gross_profit", ascending=False)
        .head(5)[["sku_name", "gross_profit"]]
        .itertuples(index=False, name=None)
    )
    s.top_profit = [(name, round(float(v), 2)) for name, v in top]
    return s


def build_alerts(df: pd.DataFrame, prev_df: pd.DataFrame | None = None) -> list[Alert]:
    """按规则生成预警。规则阈值全部来自配置，方便业务方自己调。"""
    alerts: list[Alert] = []

    # 规则 1：毛利为负 —— 卖一件亏一件，最高优先级
    for r in df[df["gross_profit"] < 0].itertuples():
        alerts.append(Alert(
            level="high", alert_type="negative_profit", sku_id=r.sku_id, sku_name=r.sku_name,
            message=f"毛利 {r.gross_profit:.2f} 元，卖得越多亏得越多，"
                    f"重点查推广费 {r.ad_cost:.2f} / 佣金 {r.commission:.2f}",
        ))

    # 规则 2：毛利率过低（但不亏损）
    low = df[(df["gross_profit"] >= 0) & (df["gross_margin"] < settings.gm_alert)]
    for r in low.itertuples():
        alerts.append(Alert(
            level="mid", alert_type="low_margin", sku_id=r.sku_id, sku_name=r.sku_name,
            message=f"毛利率 {r.gross_margin:.1%}，低于 {settings.gm_alert:.0%} 红线",
        ))

    # 规则 3：库存风险（滞销 / 断货）
    for r in df[df["stock_days"] > settings.stock_days_max].itertuples():
        alerts.append(Alert(
            level="mid", alert_type="over_stock", sku_id=r.sku_id, sku_name=r.sku_name,
            message=f"库存 {r.stock_qty} 件，按当前销速可卖 {r.stock_days:.0f} 天，建议清库存",
        ))
    for r in df[(df["stock_days"] < settings.stock_days_min) & (df["qty_sold"] > 0)].itertuples():
        alerts.append(Alert(
            level="high", alert_type="stock_out_risk", sku_id=r.sku_id, sku_name=r.sku_name,
            message=f"仅剩 {r.stock_qty} 件，约 {r.stock_days:.0f} 天售罄，需补货",
        ))

    # 规则 4：退货率超标
    for r in df[(df["return_rate"] > settings.return_rate_alert) & (df["return_qty"] > 0)].itertuples():
        alerts.append(Alert(
            level="mid", alert_type="high_return", sku_id=r.sku_id, sku_name=r.sku_name,
            message=f"退货 {r.return_qty} 件，退货率 {r.return_rate:.1%}，查质量/尺码问题",
        ))

    # 规则 5：单品类日销环比暴跌 —— 需要历史数据，第二次跑才有意义
    if prev_df is not None and not prev_df.empty:
        prev = prev_df.set_index("sku_id")["qty_sold"]
        cur = df.set_index("sku_id")["qty_sold"]
        for sku_id, qty in cur.items():
            if sku_id in prev.index and prev[sku_id] >= 5 and qty <= prev[sku_id] * 0.6:
                name = df.loc[df["sku_id"] == sku_id, "sku_name"].iloc[0]
                alerts.append(Alert(
                    level="mid", alert_type="sales_drop", sku_id=sku_id, sku_name=name,
                    message=f"销量 {int(qty)} 件，较前一日 {int(prev[sku_id])} 件下滑 "
                            f"{(1 - qty / prev[sku_id]):.0%}，查是否掉排名/断货/改价",
                ))

    # 第六条规则"推广费占 GMV > 50% → high"（5 分钟，SKU-006 占 62% 会立刻命中）   
    for r in df[df["ad_cost"] / df["gmv"] > settings.ad_ratio_alert].itertuples():
        alerts.append(Alert(
            level="high", alert_type="high_ad_ratio",  sku_id=r.sku_id, sku_name=r.sku_name,
            message=f"推广费占 GMV 超过 {settings.ad_ratio_alert:.0%}"
        ))       

    order = {"high": 0, "mid": 1, "low": 2}
    alerts.sort(key=lambda a: (order.get(a.level, 9), a.sku_id))
    log.info("预警生成完成: %d 条", len(alerts))
    return alerts
