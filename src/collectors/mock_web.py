"""采集器 B：用浏览器自动化抓页面（没有 API 时的唯一办法）。

真实场景：抖店/千牛/拼多多商家后台、生意参谋这类系统不给普通商家开放接口，
只能像人一样登录、点菜单、读表格——这就是 Selenium/Playwright 的用武之地，
也是岗位标签里"爬虫经验 + Selenium"的真实含义。

启用方式：
    pip install playwright
    python -m playwright install chromium
    .env 里设 SOURCE=mock_web
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import settings
from ..logger import get_logger
from .base import BaseCollector

log = get_logger(__name__)

class MockWebCollector(BaseCollector):
    name = "mock_web"

    def __init__(self, base_url: str | None = None, headless: bool = True) -> None:
        self.base_url = (base_url or settings.mock_api_base).rstrip("/")
        self.headless = headless

    def fetch(self, biz_date: date) -> pd.DataFrame:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # 没装依赖时给出明确指引，而不是一堆报错
            raise RuntimeError(
                "未安装 playwright。执行:\n"
                "  pip install playwright\n"
                "  python -m playwright install chromium"
            ) from exc

        url = f"{self.base_url}/report?biz_date={biz_date.isoformat()}"
        log.info("打开页面: %s", url)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            # 等表格真正渲染出来再取，别用 time.sleep 硬等
            page.wait_for_selector("#report tbody tr", timeout=15000)
            # rows = page.eval_on_selector_all("#report tbody tr", "els => els.map(tr => {"
            #                                  "const c = tr.querySelectorAll('td');"
            #                                  "return {sku_id: tr.dataset.sku,"
            #                                  "sku_name: c[0].innerText.trim(),"
            #                                  "category: c[1].innerText.trim(),"
            #                                  "qty_sold: c[2].innerText.trim(),"
            #                                  "return_qty: c[3].innerText.trim(),"
            #                                  "stock_qty: c[4].innerText.trim(),"
            #                                  "gmv: c[5].innerText.trim(),"
            #                                  "refund_amount: c[6].innerText.trim(),"
            #                                  "commission: c[7].innerText.trim(),"
            #                                  "ad_cost: c[8].innerText.trim(),"
            #                                  "shipping_cost: c[9].innerText.trim(),"
            #                                  "unit_cost: c[10].innerText.trim()};})")
            js = '''
                els => els.map(tr => {
                    const c = tr.querySelectorAll('td');
                    return {
                        sku_id: tr.dataset.sku,
                        sku_name: c[0].innerText.trim(),
                        category: c[1].innerText.trim(),
                        qty_sold: c[2].innerText.trim(),
                        return_qty: c[3].innerText.trim(),
                        stock_qty: c[4].innerText.trim(),
                        gmv: c[5].innerText.trim(),
                        refund_amount: c[6].innerText.trim(),
                        commission: c[7].innerText.trim(),
                        ad_cost: c[8].innerText.trim(),
                        shipping_cost: c[9].innerText.trim(),
                        unit_cost: c[10].innerText.trim(),
                    };
                })
            '''
            rows = page.eval_on_selector_all("#report tbody tr", js)
            browser.close()
        if not rows:
            raise RuntimeError(f"页面未取到任何数据行: {url}")
        log.info("页面采集成功: %s 行", len(rows))
        return pd.DataFrame(rows)
