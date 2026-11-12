"""定时调度入口：每天固定时间自动跑。

真实部署时这个进程常驻（Windows 用任务计划程序守护，Linux 用 systemd/supervisor，
或者直接 docker compose up -d）。

用法：python scheduler.py
"""
from __future__ import annotations

import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.logger import get_logger
from src.pipeline import run

log = get_logger(__name__)

HOUR = int(os.getenv("SCHEDULE_HOUR", "8"))
MINUTE = int(os.getenv("SCHEDULE_MINUTE", "30"))


def job() -> None:
    log.info("定时任务触发")
    run()


def main() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        job,
        CronTrigger(hour=HOUR, minute=MINUTE, day_of_week="mon-sun"),
        id="daily_report",
        max_instances=1,        # 上一轮没跑完不叠加
        coalesce=True,          # 错过多次只补跑一次
        misfire_grace_time=3600,
    )
    log.info("调度器已启动，每天 %02d:%02d 执行。Ctrl+C 退出", HOUR, MINUTE)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度器已停止")


if __name__ == "__main__":
    main()
