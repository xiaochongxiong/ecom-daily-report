"""单次执行入口（调试和手动补数据都用它）。

用法：
    python run_once.py                     # 跑昨天
    python run_once.py --date 2026-11-01   # 跑指定日期
    python run_once.py --dry-run           # 只打印，不入库不推送
    python run_once.py --source mock_web   # 换成浏览器采集
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from src.pipeline import run


def main() -> int:
    parser = argparse.ArgumentParser(description="电商经营日报 - 单次执行")
    parser.add_argument("--date", help="业务日期 YYYY-MM-DD，默认昨天")
    parser.add_argument("--source", choices=["mock_api", "mock_web", "public_site"], help="数据源")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行：只打印，不入库、不落盘、不推送")
    args = parser.parse_args()

    biz_date = None
    if args.date:
        try:
            biz_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"日期格式错误: {args.date}，应为 YYYY-MM-DD")
            return 2

    result = run(biz_date=biz_date, source=args.source, dry_run=args.dry_run)

    if not result["ok"]:
        print(f"\n执行失败: {result['error']}")
        return 1

    s = result["summary"]
    tag = "[试运行] " if result.get("dry_run") else ""
    print(f"\n{tag}执行完成: {result['biz_date']} | 处理 {result['rows']} 个 SKU | "
          f"写入 {result['rows_saved']} 行 | 耗时 {result['cost_ms']}ms")
    print(f"GMV {s.gmv:,.2f} | 毛利 {s.gross_profit:,.2f} | 毛利率 {s.gross_margin:.1%} | "
          f"预警 {len(result['alerts'])} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
