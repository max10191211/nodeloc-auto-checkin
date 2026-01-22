# -*- coding: utf-8 -*-
"""
cron: 0 */6 * * *
new Env("NodeLoc 签到")
"""
import os
from loguru import logger
from nodeloc import NodeLocRunner

if __name__ == "__main__":
    os.environ.pop("DISPLAY", None)
    os.environ.pop("DYLD_LIBRARY_PATH", None)

    runner = NodeLocRunner()
    ok = runner.run()
    if ok:
        logger.success("NodeLoc 任务完成")
    else:
        logger.error("NodeLoc 任务失败")
