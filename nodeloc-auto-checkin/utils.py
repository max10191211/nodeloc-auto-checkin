# -*- coding: utf-8 -*-
import time
import functools
from loguru import logger

def retry(retries=3, sleep_seconds=1.0):
    def deco(func):
        @functools.wraps(func)
        def wrap(*args, **kwargs):
            for i in range(1, retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if i == retries:
                        logger.error(f"{func.__name__} 最终失败：{e}")
                        raise
                    logger.warning(f"{func.__name__} 第 {i}/{retries} 次失败：{e}，等待 {sleep_seconds}s 重试")
                    time.sleep(sleep_seconds)
        return wrap
    return deco
