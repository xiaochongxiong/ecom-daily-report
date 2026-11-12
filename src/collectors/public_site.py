"""采集器 C：抓公开网页（唯一的真实网络请求）。

目标：https://books.toscrape.com/ —— 官方抓取沙箱，robots.txt 为空。
边界：只返回 3 列，不满足 clean() 的 12 列契约，**不要注册进工厂**跑主流程。
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests
from pyquery import PyQuery as pq

from ..config import settings
from ..logger import get_logger
from .base import BaseCollector

log = get_logger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (compatible; ecom-report-demo/1.0)"}


class PublicSiteCollector(BaseCollector):
    name = "public_site"

    def __init__(self, base_url: str | None = None, delay: float = 1.5) -> None:
        self.base_url = (base_url or settings.public_site_base).rstrip("/") + "/"
        self.delay = delay

    def fetch(self, biz_date: date) -> pd.DataFrame:
        resp = requests.get(self.base_url, headers=UA, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"        # 服务器没声明 charset，不指定就出 Â£ 乱码

        rows = []
        for li in pq(resp.text)("ol.row li").items():
            price_text = li(".price_color").text().strip()
            rows.append({
                "title": li("h3 > a").attr("title"),
                "price": float(price_text.replace("£", "")),   # 采集器负责转成标准格式
                "stock": li(".instock.availability").text().strip(),
            })

        time.sleep(self.delay)          # 限速，抓任何站点都要有
        if not rows:
            raise RuntimeError(f"页面未取到任何数据行: {self.base_url}")
        log.info("公开站点采集完成: %d 条", len(rows))
        return pd.DataFrame(rows)

if __name__ == "__main__":
    df = PublicSiteCollector().fetch(date.today())
    print(df.head(5).to_string())
    print(f"行数={len(df)} dtype={df.dtypes.to_dict()}")
