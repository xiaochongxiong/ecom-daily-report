"""采集器统一接口。

为什么要有这一层（这是"架构设计经验"最朴素的体现）：
  电商后台有的给 API，有的只能点页面。把采集抽象成一个接口，
  换数据源时只加一个类，主流程一行都不用改。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class BaseCollector(ABC):
    """所有采集器的父类。"""

    name: str = "base"

    @abstractmethod
    def fetch(self, biz_date: date) -> pd.DataFrame:
        """抓取指定日期的原始明细，返回 DataFrame。

        约定：不在这里做业务计算，只负责"把原始数据完整拿回来"。
        清洗和计算一律放在 transform.py，职责分离。
        """
        raise NotImplementedError
