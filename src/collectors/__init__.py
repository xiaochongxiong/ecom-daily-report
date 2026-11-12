"""采集器工厂：根据配置拿到对应实现。"""
from __future__ import annotations

from ..config import settings
from .base import BaseCollector
from .mock_api import MockApiCollector


def get_collector(source: str | None = None) -> BaseCollector:
    key = (source or settings.source).strip().lower()
    if key == "mock_api":
        return MockApiCollector()
    if key == "mock_web":
        # 延迟导入：没装 playwright 时不影响其他数据源
        from .mock_web import MockWebCollector

        return MockWebCollector()
    if key == "public_site":
        # 公开网页（无需登录）用 requests + pyquery 抓，不启浏览器
        from .public_site import PublicSiteCollector

        return PublicSiteCollector()
    raise ValueError(f"未知数据源: {key}，可选 mock_api / mock_web / public_site")
