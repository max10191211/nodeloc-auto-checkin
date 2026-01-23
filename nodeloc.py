
# -*- coding: utf-8 -*-
import os
import re
import time
import random
from typing import List, Optional

from loguru import logger
from curl_cffi import requests
from bs4 import BeautifulSoup
from DrissionPage import ChromiumOptions, Chromium
from DrissionPage.errors import BrowserConnectError
from tabulate import tabulate

from utils import retry

# ------------------ 基础配置 ------------------
BASE_URL = os.environ.get("NODELOC_BASE_URL", "https://www.nodeloc.com").rstrip("/")
LOGIN_URL = f"{BASE_URL}/login"
SESSION_URL = f"{BASE_URL}/session"
CSRF_URL = f"{BASE_URL}/session/csrf"

USERNAME = os.environ.get("NODELOC_USERNAME") or os.environ.get("USERNAME")
PASSWORD = os.environ.get("NODELOC_PASSWORD") or os.environ.get("PASSWORD")
NL_COOKIE = os.environ.get("NL_COOKIE", "").strip()

BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
# 可选：允许通过环境变量切换无头风格（new/old/auto），默认 new
HEADLESS_VARIANT = os.environ.get("HEADLESS_VARIANT", "new").strip().lower()
LIKE_PROB = float(os.environ.get("LIKE_PROB", "0.3"))
CLICK_COUNT = int(os.environ.get("CLICK_COUNT", "10"))
DEBUG_ARTIFACTS = os.environ.get("DEBUG_ARTIFACTS", "false").strip().lower() == "true"

# 默认签到按钮选择器：优先你提供的精准结构，其次兜底
DEFAULT_CHECKIN_SELECTORS = (
    "li.header-dropdown-toggle.checkin-icon button.checkin-button,"
    "button.checkin-button:not(.checked-in),"
    "button.checkin-button"
)
CHECKIN_SELECTOR = os.environ.get("CHECKIN_SELECTOR", DEFAULT_CHECKIN_SELECTORS).strip()

GOTIFY_URL = os.environ.get("GOTIFY_URL")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")
SC3_PUSH_KEY = os.environ.get("SC3_PUSH_KEY")
# ----------------------------------------------------


def _detect_chrome_path() -> Optional[str]:
    """在常见路径中寻找 Chromium/Chrome 二进制；优先用 CHROME_PATH。"""
    env_path = os.environ.get("CHROME_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/opt/google/chrome/chrome",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _make_chromium(headless: bool, headless_variant: str = "new") -> Chromium:
    """
    创建稳定的 Chromium：
    - auto_port(True)：避免固定 9222 端口冲突与用户目录冲突
    - 容器友好参数：--no-sandbox / --disable-dev-shm-usage / --disable-gpu 等
    - headless_variant: "new" 或 "old"
    """
    co = ChromiumOptions(read_file=False)

    # 自动分配端口 + 独立临时用户目录
    co.auto_port(True)

    # 指定浏览器路径（优先环境变量）
    chrome_path = _detect_chrome_path()
    if chrome_path:
        co.set_browser_path(chrome_path)

    # 容器/Linux 常用稳定参数
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-software-rasterizer")
    co.set_argument("--disable-extensions")
    co.set_argument("--mute-audio")
    co.set_argument("--window-size", "1920,1080")
    co.set_tmp_path("/tmp/DrissionPage")
    co.incognito(True)
    co.set_timeouts(page_load=30)

    # 无头模式（显式控制 new/old）
    if headless:
        if headless_variant == "new":
            co.set_argument("--headless", "new")
        elif headless_variant == "old":
            co.set_argument("--headless", "old")
        else:
            co.set_argument("--headless", "new")

    # 桌面渲染/反自动化检测的原有参数（保留你的设置）
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--disable-features=IsolateOrigins,site-per-process")

    return Chromium(co)


class NodeLocBrowser:
    def __init__(self) -> None:
        logger.info(f"Using BASE_URL: {BASE_URL}")

        # HTTP 会话（curl_cffi）
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/118.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

        # ---------- 稳健启动 Chromium ----------
        try:
            variant = HEADLESS_VARIANT or "new"
            self.browser = _make_chromium(HEADLESS, variant)
        except BrowserConnectError:
            if HEADLESS and (HEADLESS_VARIANT in ("", "new", "auto")):
                # 少量环境/版本对 old 更友好，自动回退一次
                self.browser = _make_chromium(True, "old")
            else:
                raise

        self.page = self.browser.new_tab()

    # ------------------ Cookie/Login ------------------
    def set_cookies_to_both(self, cookie_dict: dict):
        for k, v in cookie_dict.items():
            self.session.cookies.set(k, v, domain=self._cookie_domain())
        dp_cookies = [{"name": k, "value": v, "domain": self._cookie_domain(), "path": "/"}
                      for k, v in cookie_dict.items()]
        # DrissionPage 的 set.cookies 接口
        self.page.set.cookies(dp_cookies)

    def _cookie_domain(self) -> str:
        host = BASE_URL.split("://", 1)[-1].split("/", 1)[0]
        if host.startswith("www."):
            host = host[4:]
        return f".{host}"

    def _parse_cookie_str(self, cookie_str: str) -> dict:
        pairs = [kv.strip() for kv in cookie_str.split(";") if "=" in kv]
        return {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip() for kv in pairs}

    def _server_current_user(self) -> str:
        """服务端获取当前登录用户名（尽量不依赖前端），优先 /u 解析 data-user-card。"""
        try:
            r = self.session.get(f"{BASE_URL}/u", impersonate="chrome136", timeout=10)
            if r.status_code == 200:
                m = re.search(r'data-user-card="([^"]+)"', r.text or "")
                if m:
                    return m.group(1)
        except Exception:
            pass
        return ""

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
            ok = self._verify_logged_in()
            if ok:
                who = self._server_current_user()
                logger.info(f"[after-login(cookie)] server current user = {who or '未知'}")
            return ok
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
                logger.error(f"登录失败，状态码: {resp_login.status_code}")
                return False
            j = resp_login.json()
            if j.get("error"):
                logger.error(f"登录失败: {j.get('error')}")
                return False

            self.set_cookies_to_both(self.session.cookies.get_dict())
            self.page.get(BASE_URL + "/")
            time.sleep(4)
            ok = self._verify_logged_in()
            who = self._server_current_user()
            logger.info(f"[after-login(password)] server current user = {who or '未知'}")
            return ok
        except Exception as e:
            logger.error(f"密码登录异常: {e}")
            return False

    def _verify_logged_in(self) -> bool:
        # 优先标准 CSS 写法
        user_ele = self.page.ele("css=#current-user") or self.page.ele("@id=current-user")
        if user_ele:
            logger.info("登录验证成功（current-user）")
            return True
        html = self.page.html or ""
        if "avatar" in html or "/u/" in html:
            logger.info("登录验证成功（avatar / /u/）")
            return True
        logger.error("登录验证失败")
        return False
    # ----------------------------------------------------

    # ------------------ 签到（Desktop 版 + whoami/cookies/server verify） ------------------
    def try_checkin(self) -> bool:
        logger.info("尝试执行签到...")

        self.page.get(BASE_URL + "/")
        time.sleep(3)

        # whoami（从 DOM 读取当前用户）
        try:
            me = self.page.ele("css=div.directory-table__row.me a[data-user-card]") \
                 or self.page.ele("css=a[data-user-card]")
            uname = me.attr("data-user-card") if me else ""
            logger.info(f"[whoami] 当前页面用户：{uname or '未知'}  @ {BASE_URL}")
        except Exception:
            pass

        # 打印浏览器内 Cookie（域/路径/关键名）
        try:
            cks = self.page.cookies(as_dict=False)  # list of dict
            brief = []
            for c in (cks or []):
                domain = c.get("domain", "?")
                path = c.get("path", "/")
                name = c.get("name", "?")
                val = (c.get("value", "") or "")[:12]
                brief.append(f"{domain} {path} {name}={val}...")
            if brief:
                logger.debug("[cookies] " + " | ".join(brief))
        except Exception:
            pass

        # 等待 Desktop 顶部导航栏渲染完成
        try:
            self.page.wait.ele_displayed("css=ul.icons.d-header-icons", timeout=10)
        except Exception:
            logger.warning("顶部导航栏未完全渲染，可能导致找不到签到按钮")

        # 精准按钮 + 更稳备选（统一用 css= 前缀）
        selectors = [
            "li.header-dropdown-toggle.checkin-icon button.checkin-button",  # 你的 DOM
            "li.checkin-icon button.checkin-button",                         # 略宽松
            'button.checkin-button[title*="签到"]',                          # 利用 title 文案
            'button.checkin-button[aria-label*="签到"]',                     # 利用 aria-label 文案
        ]
        # 允许通过环境变量覆盖/追加
        env_sel = [s.strip() for s in (CHECKIN_SELECTOR or "").split(",") if s.strip()]
        for s in env_sel:
            if s not in selectors:
                selectors.append(s)

        logger.debug(f"签到按钮候选（CSS）：{selectors}")

        def _checked(ele) -> bool:
            """更稳的已签到判断：class 或 文案（title/aria-label）"""
            try:
                cls = ele.attr("class") or ""
                title = (ele.attr("title") or "") + " " + (ele.attr("aria-label") or "")
                return ("checked-in" in cls) or ("已签" in title) or ("已签到" in title)
            except Exception:
                return False

        def server_side_verify(session, base_url: str) -> str:
            """服务端侧核验：抓 /u 与 /badges，确认登录用户与可访问性"""
            try:
                r1 = session.get(f"{base_url}/u", impersonate="chrome136", timeout=10)
                hit_user = ""
                if r1.status_code == 200:
                    m = re.search(r'data-user-card="([^"]+)"', r1.text or "")
                    hit_user = m.group(1) if m else ""
                r2 = session.get(f"{base_url}/badges", impersonate="chrome136", timeout=10)
                code2 = r2.status_code
                return f"[server] user={hit_user or '未知'} /badges_code={code2}"
            except Exception as e:
                return f"[server] verify error: {e}"

        # 逐个候选尝试
        for sel in selectors:
            btn = None
            try:
                btn = self.page.ele(f"css={sel}")   # 关键：DrissionPage 使用 css= 前缀
            except Exception:
                btn = None

            if not btn:
                logger.debug(f"未找到：{sel}")
                continue

            if _checked(btn):
                logger.success("今日已签到（checked-in / 文案提示）")
                # --------- 增强校验：服务端 + UI 双确认 ----------
                logger.info(server_side_verify(self.session, BASE_URL))
                who2 = self._server_current_user()
                logger.info(f"[after-checkin] server current user = {who2 or '未知'}")

                # 刷新首页再次确认按钮状态
                self.page.get(BASE_URL + "/")
                time.sleep(2)
                final_btn = self.page.ele("css=li.checkin-icon button.checkin-button") \
                            or self.page.ele("css=button.checkin-button")
                if final_btn:
                    final_cls = final_btn.attr("class") or ""
                    logger.info(f"[final-ui] checkin-button classes: {final_cls}")
                if DEBUG_ARTIFACTS:
                    try:
                        self.page.save_screenshot("/app/snap_after.png")
                    except Exception:
                        pass
                # ----------------------------------------------
                return True

            # 点击（失败则 JS 兜底）
            try:
                btn.click()
            except Exception:
                try:
                    self.page.run_js("arguments[0].click();", btn)
                except Exception as e:
                    logger.error(f"点击失败：{e}")
                    continue

            time.sleep(2)

            # 二次确认
            btn2 = self.page.ele(f"css={sel}")
            if btn2 and _checked(btn2):
                logger.success("签到成功（状态/文案已更新）")
                # --------- 增强校验：服务端 + UI 双确认 ----------
                logger.info(server_side_verify(self.session, BASE_URL))
                who2 = self._server_current_user()
                logger.info(f"[after-checkin] server current user = {who2 or '未知'}")

                self.page.get(BASE_URL + "/")
                time.sleep(2)
                final_btn = self.page.ele("css=li.checkin-icon button.checkin-button") \
                            or self.page.ele("css=button.checkin-button")
                if final_btn:
                    final_cls = final_btn.attr("class") or ""
                    logger.info(f"[final-ui] checkin-button classes: {final_cls}")
                if DEBUG_ARTIFACTS:
                    try:
                        self.page.save_screenshot("/app/snap_after.png")
                    except Exception:
                        pass
                # ----------------------------------------------
                return True

        # 走到这里：仍未确认成功 → 导出调试信息
        try:
            with open("/app/debug_page.html", "w", encoding="utf-8") as f:
                f.write(self.page.html or "")
            try:
                self.page.save_screenshot("/app/snap.png")
            except Exception:
                pass
            icons = self.page.ele("css=ul.icons.d-header-icons")
            if icons:
                logger.debug(f"[debug] header icons HTML: {icons.html}")
        except Exception:
            pass

        logger.info(server_side_verify(self.session, BASE_URL))
        logger.warning("未找到签到按钮或未确认到成功（已尝试导出 /app/debug_page.html 与 /app/snap.png）")
        return False
    # ----------------------------------------------------

    # ------------------ 浏览/点赞 ------------------
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

            at_bottom = page.run_js(
                "return window.scrollY + window.innerHeight >= document.body.scrollHeight;"
            )
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
    # ----------------------------------------------------

    # ------------------ 信息与推送 ------------------
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

        # Gotify
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

        # Server酱³
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
    # ----------------------------------------------------

    # ------------------ 入口 ------------------
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
    # ----------------------------------------------------


class NodeLocRunner:
    def run(self) -> bool:
        b = NodeLocBrowser()
        return b.run()
