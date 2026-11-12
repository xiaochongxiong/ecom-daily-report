"""全局配置。

设计要点：所有可变参数都通过环境变量注入，代码里不写死。
这样本地用 SQLite、线上用 PostgreSQL，只改 .env，不动一行代码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    source: str
    mock_api_base: str
    public_site_base: str
    feishu_webhook: str
    gm_alert: float
    stock_days_max: float
    stock_days_min: float
    ad_ratio_alert: float
    return_rate_alert: float
    return_cost_recovery: float      # 退货商品成本可回收比例 0~1，见 transform.py 口径说明
    refund_commission_on_return: bool  # 平台对退款订单是否退佣
    data_dir: Path
    log_dir: Path
    output_dir: Path
    push_policy: str

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.log_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)


def _build_database_url() -> str:
    raw = os.getenv("DATABASE_URL", "sqlite:///./data/report.db")
    # 把相对路径的 SQLite 转成基于项目根目录的绝对路径，避免从别的目录启动时找不到库
    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
        rel = raw.replace("sqlite:///", "", 1)
        p = (BASE_DIR / rel).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{p.as_posix()}"
    return raw


settings = Settings(
    database_url=_build_database_url(),
    source=os.getenv("SOURCE", "mock_api").strip().lower(),
    mock_api_base=os.getenv("MOCK_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
    public_site_base=os.getenv("PUBLIC_SITE_BASE", "https://books.toscrape.com/").rstrip("/"),
    feishu_webhook=os.getenv("FEISHU_WEBHOOK", "").strip(),
    gm_alert=_get_float("GM_ALERT", 0.15),
    stock_days_max=_get_float("STOCK_DAYS_MAX", 45),
    ad_ratio_alert=_get_float("AD_RATIO_ALERT", 0.5),
    stock_days_min=_get_float("STOCK_DAYS_MIN", 7),
    return_rate_alert=_get_float("RETURN_RATE_ALERT", 0.10),
    # 0.0 = 退货即报废（内衣/内裤/美妆等贴身或开封类目，行业惯例不可二次销售）
    # 0.5 = 退货可折价清仓；1.0 = 退货完好入库可原价再售
    return_cost_recovery=_get_float("RETURN_COST_RECOVERY", 0.0),
    refund_commission_on_return=_get_bool("REFUND_COMMISSION_ON_RETURN", True),
    data_dir=BASE_DIR / "data",
    log_dir=BASE_DIR / "logs",
    output_dir=BASE_DIR / "output",
    push_policy=os.getenv("PUSH_POLICY", "always").strip().lower(),
)
