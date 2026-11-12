"""数据清洗 + 指标计算（整个项目最值钱的部分）。

毛利是最容易算错的一环：口径不写清楚，报表就没人敢用。
这里的口径写成明确的公式和假设，业务方才能认可、才能核对。

口径定义（v2，2026-11-04 修订）
---------
收入      gmv              成交金额（不含运费收入）
退款      refund_amount    已退款金额（从收入里扣掉）
商品成本  cost_of_goods    unit_cost × 净销售件数
                          净销售件数 = qty_sold − return_qty × 回收率
平台佣金  commission       数据源给定；平台对退款订单退佣时按退货率净额化
推广费    ad_cost          直通车/千川等广告花费，按 SKU 归集（退款不退推广费）
履约成本  shipping_cost    快递费 + 包材（已发出的运费不退）

毛利      = gmv - refund_amount - commission - ad_cost - shipping_cost - cost_of_goods
毛利率    = 毛利 / gmv

为什么必须区分"退货成本回收率"
------------------------------
退货有两种，成本处理完全相反，混用会直接算错一家店赚不赚钱：

1) 退货报废（内衣/内裤/食品/开封类目，行业惯例不可二次销售）
   货退回来了但卖不掉，成本是**真损失** → RETURN_COST_RECOVERY=0.0
   全部退货时（平台退佣，REFUND_COMMISSION_ON_RETURN=True，默认）：
       收入与佣金同时归零 → 毛利 = −(商品成本 + 运费 + 推广费)
   若平台不退佣（REFUND_COMMISSION_ON_RETURN=False）：
       佣金仍需承担 → 毛利 = −(商品成本 + 佣金 + 运费 + 推广费)
   这不是算错，这就是贴身衣物类目退货率的杀伤力所在。

2) 退货完好入库（外穿服装/3C/家居）
   成本可以回冲，真实损失只有运费和推广费 → RETURN_COST_RECOVERY=1.0
   全部退货时（平台退佣）：收入与佣金同时归零 → 毛利 ≈ −(运费 + 推广费)
   若平台不退佣：佣金仍需承担 → 毛利 ≈ −(佣金 + 运费 + 推广费)

已知简化（真实接入时需补齐）
--------------------------------------------------
1. 退回商品的二次成本（退回运费、质检、折价清仓损失）未计入
2. 推广费若无法精确归集到 SKU，应按 GMV 占比分摊
3. 未计入仓储费、人工费、平台活动扣点
4. 退款按整单处理；部分退款、仅退运费等场景需按退款明细拆分
"""
from __future__ import annotations

import pandas as pd

from .config import settings
from .logger import get_logger

log = get_logger(__name__)

# 原始字段 → 数值型
_NUMERIC_COLS = [
    "qty_sold", "return_qty", "stock_qty", "gmv", "refund_amount",
    "commission", "ad_cost", "shipping_cost", "unit_cost",
]
_REQUIRED_COLS = ["sku_id", "sku_name", "category", *_NUMERIC_COLS]


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    """把脏数据洗成可计算的规整表。"""
    if raw.empty:
        raise ValueError("原始数据为空，停止处理")

    missing = [c for c in _REQUIRED_COLS if c not in raw.columns]
    if missing:
        raise ValueError(f"原始数据缺字段: {missing}")

    df = raw.copy()

    # 1) 去空格、去 BOM（网页和 Excel 导出最常见的脏数据）
    for col in ("sku_id", "sku_name", "category"):
        df[col] = df[col].astype(str).str.replace("\ufeff", "*", regex=False).str.strip()

    # 2) 金额里可能带逗号、货币符号，统一转数值
    for col in _NUMERIC_COLS:
        df[col] = (
            df[col].astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("￥", "", regex=False)
            .str.replace("¥", "", regex=False)
            .str.replace("£", "", regex=False)
            .str.replace("--", "0", regex=False)
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 3) 空值填充：数量类填 0，成本价缺失单独提示（影响毛利准确性）
    df[_NUMERIC_COLS] = df[_NUMERIC_COLS].fillna(0)
    if (df["unit_cost"] <= 0).any():
        bad = df.loc[df["unit_cost"] <= 0, "sku_id"].tolist()
        log.warning("以下 SKU 成本价为 0 或缺失，毛利会偏高: %s", bad)

    # 4) 去重：同一 SKU 同一天只保留最后一条（按业务侧"后写覆盖"惯例）
    before = len(df)
    df = df.drop_duplicates(subset=["sku_id"], keep="last")
    if len(df) != before:
        log.warning("发现重复 SKU，已去重 %d 行", before - len(df))

    # 5) 业务校验：数量不能为负
    neg = df[(df["qty_sold"] < 0) | (df["return_qty"] < 0)]
    if not neg.empty:
        log.warning("发现负数数量，已修正为 0: %s", neg["sku_id"].tolist())
        df.loc[df["qty_sold"] < 0, "qty_sold"] = 0
        df.loc[df["return_qty"] < 0, "return_qty"] = 0

    # 6) 退货数不可能大于发货数（数据源串号时会出这种错）
    df["return_qty"] = df[["return_qty", "qty_sold"]].min(axis=1)

    return df.reset_index(drop=True)


def enrich(df: pd.DataFrame, recovery: float | None = None) -> pd.DataFrame:
    """计算成本、毛利、毛利率、退货率等派生指标。

    recovery: 退货成本回收率 0~1；不传则取配置 RETURN_COST_RECOVERY。
              显式传参是为了让单测能同时覆盖"报废"和"可再售"两种口径。
    """
    out = df.copy()
    rate = settings.return_cost_recovery if recovery is None else float(recovery)
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"退货成本回收率必须在 0~1 之间，当前 {rate}")

    # 退货率先算：后面佣金净额化要用到
    out["return_rate"] = (out["return_qty"] / out["qty_sold"].replace(0, pd.NA)).fillna(0).round(4)

    # 成本只覆盖"真正卖掉、且不会退回仓库"的件数
    #   rate=0   退货全报废，成本按发货件数全额计（默认，贴身衣物类目）
    #   rate=1   退货全可再售，成本按净销售件数计
    net_qty = (out["qty_sold"] - out["return_qty"] * rate).clip(lower=0)
    out["cost_of_goods"] = (out["unit_cost"] * net_qty).round(2)

    # 平台一般对退款订单退还技术服务费；不退佣的平台把 REFUND_COMMISSION_ON_RETURN 关掉
    if settings.refund_commission_on_return:
        out["commission"] = (out["commission"] * (1 - out["return_rate"])).round(2)

    out["gross_profit"] = (
        out["gmv"]
        - out["refund_amount"]
        - out["commission"]
        - out["ad_cost"]
        - out["shipping_cost"]
        - out["cost_of_goods"]
    ).round(2)

    # 分母为 0 时用 pandas 的安全写法，避免 ZeroDivisionError / inf
    out["gross_margin"] = (out["gross_profit"] / out["gmv"].replace(0, pd.NA)).fillna(0).round(4)

    # 库存周转天数 = 期末库存 / 日均销量（单日数据用当日销量估算，趋势看多日）
    out["stock_days"] = (out["stock_qty"] / out["qty_sold"].replace(0, pd.NA)).fillna(999).round(1)

    # 动销率 = 本期卖出 / 期初可供货量（期末库存 + 已卖出），值域 [0,1]
    # 分母为 0 = 既没货也没卖，业务上记 0；绝不能出 NaN / inf 流进报表
    available = out["stock_qty"] + out["qty_sold"]
    out["sell_through_rate"] = (
        out["qty_sold"] / available.where(available != 0)
    ).fillna(0).round(4)

    return out


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    """清洗 + 计算，一步到位。"""
    df = enrich(clean(raw))
    log.info("清洗完成: %d 个 SKU，GMV 合计 %.2f", len(df), df["gmv"].sum())
    return df
