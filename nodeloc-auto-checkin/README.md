![CI](https://github.com/max10191211/nodeloc-auto-checkin/actions/workflows/checkin.yml/badge.svg)

# NodeLoc Auto Check-in
自动签到 + 随机浏览/点赞 + 推送通知
基于 **Python + curl_cffi + DrissionPage（可控浏览器）**

---

## ✨ 功能特性
- **多方式登录**：`NL_COOKIE`（推荐）或 用户名+密码（自动处理 CSRF）
- **全自动签到**：智能查找/点击签到按钮，支持自定义 CSS 选择器
- **模拟活跃**：随机浏览、滚动、可选点赞（概率可配）
- **通知**：Gotify、Server酱³
- **部署**：本地 / Docker / 青龙 / GitHub Actions
- **域名可配**：默认 `https://www.nodeloc.com`，可改 `https://nodeloc.cc`

## 🛠️ 安装与运行
### 本地
python -m venv .venv && .\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python .\\main.py

### Docker
docker build -t nodeloc-auto-checkin .
docker run --rm ^
  -e NODELOC_BASE_URL="https://www.nodeloc.com" ^
  -e NL_COOKIE="你的COOKIE" ^
  -e BROWSE_ENABLED=true ^
  -e LIKE_PROB=0.3 ^
  nodeloc-auto-checkin

### 青龙（QingLong）
脚本已内置
"""
cron: 0 */6 * * *
new Env("NodeLoc 签到")
"""
配置与 .env.example 同名变量即可。

### GitHub Actions
在仓库 Settings → Secrets and variables → Actions：
- 至少其一：`NL_COOKIE`（推荐）或 `NODELOC_USERNAME` + `NODELOC_PASSWORD`
- 可选：`NODELOC_BASE_URL`、`GOTIFY_URL`、`GOTIFY_TOKEN`、`SC3_PUSH_KEY`
进入 Actions 手动 Run workflow 一次后按 CRON 自动运行。

## ⚙️ 环境变量
| 变量名 | 必需 | 描述 |
|---|---|---|
| NODELOC_BASE_URL | 否 | 站点根域名，默认 https://www.nodeloc.com |
| NL_COOKIE | 建议 | 整串 Cookie（优先） |
| NODELOC_USERNAME | 否 | 用户名（未提供 NL_COOKIE 时与密码一起） |
| NODELOC_PASSWORD | 否 | 密码 |
| BROWSE_ENABLED | 否 | 是否随机浏览/点赞，默认 true |
| LIKE_PROB | 否 | 点赞概率 0~1，默认 0.3 |
| CLICK_COUNT | 否 | 随机浏览帖子数，默认 10 |
| CHECKIN_SELECTOR | 否 | 自定义签到按钮 CSS（逗号分隔多个） |
| GOTIFY_URL / GOTIFY_TOKEN | 否 | Gotify 推送 |
| SC3_PUSH_KEY | 否 | Server酱³ |
| HEADLESS | 否 | 无头模式，默认 true |

## 📌 原理
- Discourse 登录流：先 `GET /session/csrf` 再 `POST /session`
- curl_cffi 仿真浏览器 UA；DrissionPage 控制 Chromium 执行真实点击/滚动
- Cookie 同步到会话与浏览器；失败自动重试

## 🔍 FAQ
- 只签到：`BROWSE_ENABLED=false`
- 找不到签到按钮：用 F12 获取精确选择器填到 `CHECKIN_SELECTOR`
- 站点域变更：改 `NODELOC_BASE_URL` 即可

## 📜 免责声明
仅供学习与个人自动化使用；请遵守站点规则与法律；风险自负。

## 📄 License
MIT
