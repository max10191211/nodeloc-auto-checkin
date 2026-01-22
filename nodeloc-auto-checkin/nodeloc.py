# -*- coding: utf-8 -*-
import os
import re
import time
import random
from typing import List

from loguru import logger
from curl_cffi import requests
from bs4 import BeautifulSoup
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

from utils import retry

BASE_URL = os.environ.get("NODELOC_BASE_URL", "https://www.nodeloc.com").rstrip("/")
LOGIN_URL = f"{BASE_URL}/login"
SESSION_URL = f"{BASE_URL}/session"
CSRF_URL = f"{BASE_URL}/session/csrf"

USERNAME = os.environ.get("NODELOC_USERNAME") or os.environ.get("USERNAME")
PASSWORD = os.environ.get("NODELOC_PASSWORD") or os.environ.get("PASSWORD")
NL_COOKIE = os.environ.get("NL_COOKIE", "").strip()

BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false","0","off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false","0","off"]
LIKE_PROB = float(os.environ.get("LIKE_PROB", "0.3"))
CLICK_COUNT = int(os.environ.get("CLICK_COUNT", "10"))
CHECKIN_SELECTOR = os.environ.get("CHECKIN_SELECTOR", "").strip()

GOTIFY_URL = os.environ.get("GOTIFY_URL")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")
SC3_PUSH_KEY = os.environ.get("SC3_PUSH_KEY")

class NodeLocBrowser:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/118.0.0.0 Safari/537.36"),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        co = (ChromiumOptions()
              .headless(HEADLESS)
              .incognito(True)
              .set_argument("--no-sandbox")
              .set_argument("--disable-dev-shm-usage"))
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()

    def set_cookies_to_both(self, cookie_dict: dict):
        for k, v in cookie_dict.items():
            self.session.cookies.set(k, v, domain=self._cookie_domain())
        dp_cookies = [{"name": k, "value": v, "domain": self._cookie_domain(), "path": "/"}
                      for k, v in cookie_dict.items()]
        self.page.set.cookies(dp_cookies)

    def _cookie_domain(self) -> str:
        host = BASE_URL.split("://", 1)[-1].split("/", 1)[0]
        if host.startswith("www."):
            host = host[4:]
        return f".{host}"

    def _parse_cookie_str(self, cookie_str: str) -> dict:
        pairs = [kv.strip() for kv in cookie_str.split(";") if "=" in kv]
        return {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip() for kv in pairs}

    def login_via_cookie(self) -> bool:
        logger.info("尝试使用 NL_COOKIE 登录...")
        try:
            cookie_dict = self._parse_cookie_str(NL_COOKIE)
            if not cookie_dict:
                logger.warning("NL_COOKIE 为空或格式不正确")
                return False
            self.set_cookies_to_both(cookie_dict)
            self.page.get(BASE_URL + "/")
            time.sleep(3)
            return self._verify_logged_in()
        except Exception as e:
            logger.error(f"Cookie 登录异常: {e}")
            return False

    def login_via_password(self) -> bool:
        logger.info("尝试使用 用户名/密码 登录...")
        if not USERNAME or not PASSWORD:
            logger.error("未提供用户名或密码，无法使用密码登录")
            return False
        try:
            headers = {
                "User-Agent": self.session.headers["User-Agent"],
                "Accept": self.session.headers["Accept"],
                "Accept-Language": self.session.headers["Accept-Language"],
                "X-Requested-With": "XMLHttpRequest",
                "Referer": LOGIN_URL,
            }
            resp_csrf = self.session.get(CSRF_URL, headers=headers, impersonate="chrome136")
            csrf = resp_csrf.json().get("csrf")
            if not csrf:
                logger.error("未获取到 CSRF")
                return False
            logger.info(f"CSRF: {csrf[:10]}...")

            headers.update({
                "X-CSRF-Token": csrf,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": BASE_URL,
            })
            data = {"login": USERNAME, "password": PASSWORD}
            resp_login = self.session.post(SESSION_URL, data=data, headers=headers, impersonate="chrome136")
            if resp_login.status_code != 200:
                logger.error(f"登录失败，状态码: {resp_login.status_code}，响应: {resp_login.text[:200]}")
                return False
            j = resp_login.json()
            if j.get("error"):
                logger.error(f"登录失败: {j.get('error')}")
                return False

            self.set_cookies_to_both(self.session.cookies.get_dict())
            self.page.get(BASE_URL + "/")
            time.sleep(4)
            return self._verify_logged_in()
        except Exception as e:
            logger.error(f"密码登录异常: {e}")
            return False

    def _verify_logged_in(self) -> bool:
        user_ele = self.page.ele("@id=current-user")
        if user_ele:
            logger.info("登录验证成功（current-user）")
            return True
        if "avatar" in (self.page.html or "") or "/u/" in (self.page.html or ""):
            logger.info("登录验证成功（avatar / /u/）")
            return True
        logger.error("登录验证失败")
        return False

    def try_checkin(self) -> bool:
        logger.info("尝试执行签到...")
        self.page.get(BASE_URL + "/")
        time.sleep(3)

        selectors: List[str] = []
        if CHECKIN_SELECTOR:
            selectors.extend([s.strip() for s in CHECKIN_SELECTOR.split(",") if s.strip()])
        fallback_selectors = [
            'button:has-text("签到")', 'a:has-text("签到")',
            'button:has-text("打卡")', 'a:has-text("打卡")',
            '.btn.btn-primary', '.d-header .btn',
        ]
        selectors += fallback_selectors

        for css in selectors:
            try:
                ele = self.page.ele(css)
                if not ele:
                    continue
                text = (ele.text or "").strip()
                if text and any(k in text for k in ("签到", "打卡", "签 到", "Check-in", "check in")):
                    logger.info(f"发现签到按钮：{css} / 文本：{text}，准备点击")
                    ele.click()
                    time.sleep(random.uniform(1.5, 2.8))
                    logger.success("签到点击完成（如站点有提示请在站内确认）")
                    return True
                ele.click()
                time.sleep(1.5)
                logger.info(f"已点击可能的签到按钮：{css}")
                return True
            except Exception as e:
                logger.debug(f"签到点击失败，尝试下一个：{css} | {e}")
        logger.warning("未找到‘签到/打卡’按钮，可设置 CHECKIN_SELECTOR 精确匹配")
        return False

    def click_topics_and_browse(self) -> bool:
        logger.info("开始随机浏览首页主题...")
        self.page.get(BASE_URL + "/")
        time.sleep(4)
        topic_links = [a.attr("href") for a in self.page.eles("css=#list-area a.title") if a.attr("href")]
        if not topic_links:
            logger.error("未找到主题链接")
            return False
        picks = random.sample(topic_links, min(CLICK_COUNT, len(topic_links)))
        logger.info(f"发现 {len(topic_links)} 个主题，随机浏览 {len(picks)} 个")
        for url in picks:
            full = url if url.startswith("http") else (BASE_URL + url)
            self._browse_one_topic(full)
        return True

    @retry(3, sleep_seconds=1.0)
    def _browse_one_topic(self, url: str):
        tab = self.browser.new_tab()
        tab.get(url)
        time.sleep(random.uniform(1.2, 2.2))
        if random.random() < LIKE_PROB:
            self._try_like(tab)
        self._auto_scroll(tab)
        tab.close()

    def _auto_scroll(self, page):
        prev_url = None
        for _ in range(random.randint(6, 10)):
            dist = random.randint(520, 700)
            page.run_js(f"window.scrollBy(0, {dist})")
            time.sleep(random.uniform(1.8, 3.5))
            at_bottom = page.run_js("window.scrollY + window.innerHeight >= document.body.scrollHeight")
            cur = page.url
            if cur != prev_url:
                prev_url = cur
            elif at_bottom and prev_url == cur:
                break
            if random.random() < 0.07:
                break

    def _try_like(self, page) -> None:
        try:
            cand = [
                ".discourse-reactions-reaction-button",
                "button.toggle-like",
                "button.btn-like",
            ]
            for sel in cand:
                btn = page.ele(f"css={sel}")
                if btn:
                    btn.click()
                    time.sleep(random.uniform(0.8, 1.6))
                    return
        except Exception:
            pass

    def print_basic_info(self):
        try:
            resp = self.session.get(f"{BASE_URL}/badges", impersonate="chrome136")
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table tr")
            info = []
            for r in rows:
                cols = [c.text.strip() for c in r.select("td")]
                if len(cols) >= 2:
                    info.append(cols[:3])
            if info:
                print("------------- Badges / Info -------------")
                print(tabulate(info, headers=["列1", "列2", "列3"], tablefmt="pretty"))
        except Exception:
            pass

    def send_notifications(self, ok: bool, did_checkin: bool, browsed: bool):
        status = ("✅ 登录成功" if ok else "❌ 登录失败")
        if did_checkin:
            status += " + 签到完成"
        if browsed and BROWSE_ENABLED:
            status += " + 浏览任务完成"

        if GOTIFY_URL and GOTIFY_TOKEN:
            try:
                r = requests.post(
                    f"{GOTIFY_URL}/message",
                    params={"token": GOTIFY_TOKEN},
                    json={"title": "NODELOC", "message": status, "priority": 1},
                    timeout=10,
                )
                r.raise_for_status()
            except Exception:
                pass

        if SC3_PUSH_KEY:
            m = re.match(r"sct(\d+)t", SC3_PUSH_KEY, re.I)
            if m:
                uid = m.group(1)
                url = f"https://{uid}.push.ft07.com/send/{SC3_PUSH_KEY}"
                params = {"title": "NODELOC", "desp": status}
                for _ in range(3):
                    try:
                        r = requests.get(url, params=params, timeout=10)
                        r.raise_for_status()
                        break
                    except Exception:
                        time.sleep(random.randint(120, 240))

    def run(self) -> bool:
        ok = False
        did_checkin = False
        browsed = False
        try:
            if NL_COOKIE:
                ok = self.login_via_cookie()
                if not ok and USERNAME and PASSWORD:
                    ok = self.login_via_password()
            else:
                ok = self.login_via_password()

            if not ok:
                self.send_notifications(False, False, False)
                return False

            self.print_basic_info()
            did_checkin = self.try_checkin()
            if BROWSE_ENABLED:
                browsed = self.click_topics_and_browse()

            self.send_notifications(True, did_checkin, browsed)
            return True
        finally:
            try:
                self.page.close()
                self.browser.quit()
            except Exception:
                pass

class NodeLocRunner:
    def run(self) -> bool:
        b = NodeLocBrowser()
        return b.run()
