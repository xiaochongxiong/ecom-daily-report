"""核心业务逻辑的单元测试。

为什么作品集要带测试：面试官看到 pytest，会认为你写过会被别人维护的代码。
这几个用例覆盖的都是"最容易算错、算错代价最大"的地方。
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.transform import clean, enrich, transform


def _raw(**overrides) -> pd.DataFrame:
    base = {
        "sku_id": "SKU-001",
        "sku_name": "  测试文胸  ",
        "category": "文胸",
        "qty_sold": 10,
        "return_qty": 1,
        "stock_qty": 100,
        "gmv": 890.0,
        "refund_amount": 89.0,
        "commission": 44.5,
        "ad_cost": 100.0,
        "shipping_cost": 45.0,
        "unit_cost": 32.0,
    }
    base.update(overrides)
    return pd.DataFrame([base])


def test_clean_strips_space_and_dedupes() -> None:
    df = pd.concat([_raw(), _raw(qty_sold=13)], ignore_index=True)
    out = clean(df)
    assert len(out) == 1, "重复 SKU 应只保留一条"
    assert out.loc[0, "sku_name"] == "测试文胸"
    assert out.loc[0, "qty_sold"] == 13, "应保留最后一条（后写覆盖）"


def test_clean_handles_thousand_separator() -> None:
    out = clean(_raw(gmv="1,890.50"))
    assert out.loc[0, "gmv"] == pytest.approx(1890.50)


def test_return_qty_cannot_exceed_sold() -> None:
    out = clean(_raw(qty_sold=5, return_qty=99))
    assert out.loc[0, "return_qty"] == 5


def test_gross_profit_formula() -> None:
    # 口径 v2：成本按回收率 0 全额计（320），佣金按退货率 10% 净额化（44.5 → 40.05）
    # 毛利 = 890 - 89 - 40.05 - 100 - 45 - 320 = 295.95
    out = enrich(clean(_raw()), recovery=0.0)
    assert out.loc[0, "cost_of_goods"] == pytest.approx(320.0)
    assert out.loc[0, "gross_profit"] == pytest.approx(295.95)
    assert out.loc[0, "gross_margin"] == pytest.approx(295.95 / 890, rel=1e-3)


def _all_returned() -> pd.DataFrame:
    """卖 2 件、退 2 件：GMV 100，成本 20/件，佣金 5，推广 15，运费 9。"""
    return clean(_raw(qty_sold=2, return_qty=2, gmv=100.0, refund_amount=100.0,
                      commission=5.0, ad_cost=15.0, shipping_cost=9.0, unit_cost=20.0))


def test_full_return_scrapped_goods() -> None:
    """全退 + 退货报废（内衣/内裤）：货没了、钱退了，亏的是货值+运费+推广。"""
    out = enrich(_all_returned(), recovery=0.0)
    assert out.loc[0, "cost_of_goods"] == pytest.approx(40.0), "报废时成本不回冲"
    # 100 - 100 - 0(退佣) - 15 - 9 - 40 = -64
    assert out.loc[0, "gross_profit"] == pytest.approx(-64.0)


def test_full_return_resellable_goods() -> None:
    """全退 + 完好入库可再售：成本全额回冲，真实损失只剩运费和推广费。"""
    out = enrich(_all_returned(), recovery=1.0)
    assert out.loc[0, "cost_of_goods"] == pytest.approx(0.0), "可再售时成本应回冲"
    # 100 - 100 - 0 - 15 - 9 - 0 = -24
    assert out.loc[0, "gross_profit"] == pytest.approx(-24.0)


def test_recovery_rate_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="0~1"):
        enrich(_all_returned(), recovery=1.5)


def test_zero_gmv_not_crash() -> None:
    out = transform(_raw(gmv=0, qty_sold=0, return_qty=0, refund_amount=0,
                         commission=0, ad_cost=0, shipping_cost=0))
    assert out.loc[0, "gross_margin"] == 0
    assert out.loc[0, "return_rate"] == 0


def test_missing_column_raises() -> None:
    df = _raw().drop(columns=["commission"])
    with pytest.raises(ValueError, match="缺字段"):
        clean(df)

def test_sell_through_rate() -> None:
    # 卖 10 件、期末剩 90 件 → 期初可供 100，动销率 0.1
    out = enrich(clean(_raw(qty_sold=10, stock_qty=90)))
    assert out.loc[0, "sell_through_rate"] == pytest.approx(0.1)


def test_sell_through_rate_zero_available() -> None:
    # 既没货也没卖：0/0 无定义，业务上记 0，绝不能出 NaN / inf
    out = enrich(clean(_raw(qty_sold=0, stock_qty=0)))
    assert out.loc[0, "sell_through_rate"] == 0
