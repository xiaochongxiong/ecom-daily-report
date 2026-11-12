"""采集器 A：走 HTTP API（真实项目里最优先的方式）。

真实场景替换点：把 URL 换成聚水潭/旺店通/抖店开放平台的接口，
加上签名和 token 即可，主流程不用改。
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

from ..config import settings
from ..logger import get_logger
from .base import BaseCollector

log = get_logger(__name__)


class MockApiCollector(BaseCollector):
    name = "mock_api"

    def __init__(self, base_url: str | None = None, max_retry: int = 3, timeout: int = 10) -> None:
        self.base_url = (base_url or settings.mock_api_base).rstrip("/")
        self.max_retry = max_retry
        self.timeout = timeout

    def fetch(self, biz_date: date) -> pd.DataFrame:
        url = f"{self.base_url}/api/daily-report"
        params = {"biz_date": biz_date.isoformat()}

        last_err: Exception | None = None
        # 重试是必须的：接口抖动、限流、网络瞬断都是常态
        for attempt in range(1, self.max_retry + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
                rows = payload.get("rows", [])
                if not rows:
                    raise ValueError(f"{biz_date} 接口返回 0 行数据，请确认数据是否已生成")
                log.info("接口采集成功: %s 行, 日期=%s, 第%d次尝试", len(rows), biz_date, attempt)
                return pd.DataFrame(rows)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                wait = 2 ** (attempt - 1)  # 1s, 2s, 4s 指数退避
                log.warning("第%d次采集失败: %s，%ds 后重试", attempt, exc, wait)
                if attempt < self.max_retry:
                    time.sleep(wait)

        raise RuntimeError(f"采集失败，已重试 {self.max_retry} 次: {last_err}")
