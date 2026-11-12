"""数据库表结构（SQLAlchemy 2.0 声明式写法）。

和你熟悉的 PHP/MySQL 对应关系：
  DeclarativeBase  ≈ 建表 SQL
  Mapped[...]      ≈ 字段类型
  session.merge()  ≈ INSERT ... ON DUPLICATE KEY UPDATE
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DailySkuMetric(Base):
    """SKU 级日经营指标。

    用 (biz_date, shop_id, sku_id) 做联合主键，就是为了幂等：
    同一天的数据重复跑，靠 session.merge() 覆盖而不是插入重复行。
    这是"重复执行不会污染数据"的关键设计。
    """

    __tablename__ = "daily_sku_metric"

    biz_date: Mapped[date] = mapped_column(Date, primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sku_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    sku_name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), index=True)

    qty_sold: Mapped[int] = mapped_column(Integer, default=0)        # 发货件数
    return_qty: Mapped[int] = mapped_column(Integer, default=0)      # 退货件数
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)       # 期末库存

    gmv: Mapped[float] = mapped_column(Numeric(12, 2), default=0)             # 成交额（含运费前）
    refund_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)   # 退款金额
    commission: Mapped[float] = mapped_column(Numeric(12, 2), default=0)      # 平台佣金
    ad_cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)         # 推广费
    shipping_cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)   # 快递+包材
    cost_of_goods: Mapped[float] = mapped_column(Numeric(12, 2), default=0)   # 商品成本

    gross_profit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)    # 毛利
    gross_margin: Mapped[float] = mapped_column(Numeric(8, 4), default=0)     # 毛利率
    return_rate: Mapped[float] = mapped_column(Numeric(8, 4), default=0)      # 退货率
    # 派生比率字段：asdecimal=False 让 SQLAlchemy 读出时转成 float，与 Mapped[float] 注解一致；
    # 库里仍是精确的 numeric 类型。金额字段保持默认（读出 Decimal），防止 float 累加漂移。
    stock_days: Mapped[float] = mapped_column(Numeric(5, 1, asdecimal=False), default=0)        # 库存天数
    sell_through_rate: Mapped[float] = mapped_column(Numeric(8, 4, asdecimal=False), default=0)  # 动销率，值域 [0,1]

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


# 注意：Numeric 列（金额类）读出来是 decimal.Decimal，不是 float（Mapped[float] 只是注解，不做转换）。
# Decimal + float 会抛 TypeError，所以在 _load_day() 里统一 float(...) 再参与计算。
# 派生比率字段（stock_days / sell_through_rate）用 asdecimal=False，读出来直接是 float。
class EtlRun(Base):
    """每次任务的运行记录。

    作用：业务方问"今天怎么没收到日报"，你 3 秒就能查到是采集失败还是推送失败。
    没有这张表，排查全靠猜。
    """

    __tablename__ = "etl_run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    biz_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)  # success / failed，只表达 ETL 本身
    rows: Mapped[int] = mapped_column(Integer, default=0)
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # 只放错误原因，不混装推送状态

    # 推送终态，与 status 正交：入库成功但推送失败是真实存在且必须可查的状态。
    # ok / skipped_no_webhook（配置缺失，要修）/ skipped_by_policy（业务主动不推，正常）/ failed
    # NULL = 该字段上线前的历史记录，或 dry_run（不写库），不可当作成功统计
    push_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
