#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acore Hosting 自动续期（eooce 系基座 + 真实录制校准版）.

录制依据：agentscribe-bundle-zero.acorehosting.com_-_2026_10_12_10_33_39
(Session fa666856，27 DOM / 17 Network，续期一次成功)。录制还原的真实流程：

  dashboard                                   /renew?id=<uuid>  collide
  ├─ 可续：a.btn.btn-success, href=/renew?id=<uuid>
  ├─ 不可续：button.btn.disabled[disabled]（无链接）
  └─ 时间：div.time-remaining / Expires 行       ├─ GET /renew?id=（首次 403 系 CF 盾， clearance 后 200）
                                                ├─ 页内 Turnstile（explicit 渲染）→ 页 JS 自动
                                                │   POST /renew/confirm?id=<uuid>（空 body）
                                                ├─ 200 {"ok":true,"expires_at":"2026-10-13 17:59 UTC"}
                                                └─ #resOk 显示成功 + a.btn 返回 dashboard

认证：session-cookie，Cookie 名 `session`（域 zero.acorehosting.com，
httpOnly）。登录优先级：SESSION_COOKIE → Discord 账密（DC_EMAIL/DC_PASSWORD）。
无 OTP、无 CSRF（录制 replay_hints 确认）。

门控（先 HTTP 快捷路径，失败才开浏览器）：
  1. requests 带 session Cookie GET dashboard → 无续期链接 = NOT_TIME（免浏览器）；
  2. POST /renew/confirm?id= 返回 ok:true = SUCCESS（免浏览器）；
  3. 上述任一步遇 CF 403 / 非 JSON / 会话失效 → 浏览器全流程兜底。

过盾参数照抄 eooce（见技能 cf-managed-challenge“eooce 账号期过盾参数”一节）：
整页盾 240s(+180s)/6s 节流/连续 2 轮判定、token 读取 JS、弹窗启发式、人形点击。
TG 风格对齐上游 eooce 系。
"""

import os,re,sys,time,random,requests
try:
    from patchright.sync_api import sync_playwright
    USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    USING_PATCHRIGHT = False

# --- 环境变量 ---
SESSION_COOKIE = os.environ.get('SESSION_COOKIE') or ""  # session Cookie 值，优先（录制确认名 `session`）
DC_EMAIL     = os.environ.get('DC_EMAIL') or ""           # Discord 邮箱，Cookie 失效时回退
DC_PASSWORD  = os.environ.get('DC_PASSWORD') or ""        # Discord 密码，Cookie 失效时回退
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""         # Telegram Chat ID,可选
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""       # Telegram Bot Token,可选

BASE_URL = "https://zero.acorehosting.com"
DASHBOARD_URL = f"{BASE_URL}/dashboard"

# --- 代理配置（由工作流 shell 脚本写入 $GITHUB_ENV）---
IS_PROXY      = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER  = os.environ.get('PROXY_SERVER') or "http://127.0.0.1:1081"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

# Cloudflare 整页挑战 / Turnstile 组件共用的 iframe 选择器（eooce 同款）
CF_IFRAME_SELECTOR = 'iframe[src*="challenges.cloudflare.com"]'

# 录制确认的选择器
RENEW_LINK_SELECTOR = 'div.btn-row > a.btn.btn-success[href*="/renew"]'
REMAINING_SELECTOR = 'div.server-card > div.time-remaining'
RESOK_SELECTOR = '#resOk'

# 按运行平台选择 UA
if sys.platform == 'win32':
    DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
else:
    DEFAULT_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

# 日志
def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

def human_delay(min_s=3, max_s=7):
    """真人节奏：关键节点间 3-7s 随机停顿"""
    time.sleep(random.uniform(min_s, max_s))

STEALTH_JS = """
// 注意：使用真实 Chrome（channel="chrome"）时不要覆盖 window.chrome，
// 否则会丢失 app/csi/loadTimes 等真实特征，反而更容易被 Cloudflare 识别。
try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
} catch (e) {}
"""

def get_current_ip(proxy_server=None):
    """获取当前出口IP"""
    proxies = {"http": proxy_server, "https": proxy_server} if (proxy_server and IS_PROXY) else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        log(f"❌ 获取出口IP失败: {e}")
        return "获取失败"

def send_telegram_notification(status, old_due, new_due, current_ip="未知"):
    """发送 Telegram 通知（eooce 风）"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False

    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    account = DC_EMAIL or "Cookie登录"
    if '@' in account:
        name, domain = account.split('@', 1)
        if len(name) > 4:
            masked = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked = f"{name}@{domain}"
    else:
        masked = account[:2] + '****' if len(account) > 2 else account

    text = (
        f"🎰 Acore 续期报告\n\n"
        f"{status}\n"
        f"📧 账号: {masked}\n"
        f"⏱ 续期前到期时间：{old_due}\n"
        f"⏱ 续期后到期时间：{new_due}\n"
        f"🌐 续期使用IP: {current_ip}\n"
        f"🕒 续期时间：{now}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10, proxies=REQUESTS_PROXIES)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        log(f"❌ Telegram 通知失败: {resp.text}")
        return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False

# ================= HTTP 快捷路径（录制 API 形状） =================

def _http_session():
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA})
    if SESSION_COOKIE:
        s.cookies.set("session", SESSION_COOKIE, domain="zero.acorehosting.com", path="/")
    return s

def _looks_like_cf_block(resp):
    if resp.status_code == 403 and "cloudflare" in resp.headers.get("Server", "").lower():
        return True
    if "Just a moment" in resp.text[:5000] or "challenge-platform" in resp.text[:5000]:
        return True
    return False

def http_dashboard_state():
    """HTTP 读 dashboard。返回 (ok, renew_href, expire, remaining) truncated.

    ok=False 表示被 CF 拦或会话失效，上层转浏览器。renew_href 为 None 表示
    不可续（NOT_TIME）。"""
    if not SESSION_COOKIE:
        return False, None, "未知", "未知"
    try:
        s = _http_session()
        r = s.get(DASHBOARD_URL, timeout=20, proxies=REQUESTS_PROXIES)
        if _looks_like_cf_block(r):
            log("ℹ️ HTTP 预检被 CF 拦截，转浏览器。")
            return False, None, "未知", "未知"
        if r.status_code != 200 or "server-card" not in r.text:
            log(f"⚠️ HTTP dashboard 状态异常（{r.status_code}），疑似会话失效，转浏览器。")
            return False, None, "未知", "未知"
        html = r.text
        m = re.search(r'href="(/renew\?id=([a-f0-9-]+))"', html)
        renew_href = m.group(1) if m else None
        e = re.search(r'Expires\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC)', html, re.IGNORECASE)
        expire = e.group(1).strip() if e else "未知"
        t = re.search(r'class="time-remaining">\s*([^<]+?)\s*<', html)
        remaining = t.group(1).strip() if t else "未知"
        log(f"📅 HTTP 预检：expire={expire}，remaining={remaining}，"
            f"renew_link={'有' if renew_href else '无（未到时间）'}")
        return True, renew_href, expire, remaining
    except Exception as e:
        log(f"⚠️ HTTP 预检异常: {e}，转浏览器。")
        return False, None, "未知", "未知"

def http_confirm_renew(renew_href):
    """HTTP 直接 POST /renew/confirm。成功返回 (True, expires_at)，否则 (False, None)。"""
    if not SESSION_COOKIE or not renew_href:
        return False, None
    try:
        s = _http_session()
        # 录制：空 body POST，200 {"ok":true,"expires_at":"..."}（录制 id 仅示例，实跑用 dashboard 解析值）
        r = s.post(f"{BASE_URL}{renew_href.replace('/renew?', '/renew/confirm?')}",
                   timeout=20, proxies=REQUESTS_PROXIES)
        if _looks_like_cf_block(r):
            log("ℹ️ HTTP 确认被 CF 拦截，转浏览器。")
            return False, None
        try:
            data = r.json()
        except Exception:
            log(f"⚠️ HTTP 确认返回非 JSON（{r.status_code}），转浏览器。")
            return False, None
        if data.get("ok") is True:
            log(f"✅ HTTP 快捷续期成功：{data.get('expires_at')}")
            return True, data.get("expires_at", "未知")
        log(f"⚠️ HTTP 确认被拒：{str(data)[:200]}，转浏览器。")
        return False, None
    except Exception as e:
        log(f"⚠️ HTTP 确认异常: {e}，转浏览器。")
        return False, None

# ================= 过盾核心（eooce 参数） =================

def _get_turnstile_token(page):
    """读取 Turnstile 验证通过后生成的响应 token，未通过时返回 None（eooce 同款 JS）"""
    try:
        return page.evaluate(
            """
            () => {
                try {
                    if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                        let t = null;
                        try { t = window.turnstile.getResponse(); } catch (e) {}
                        if (t && t.length > 30) return t;
                        for (let i = 0; i < 5; i++) {
                            try { t = window.turnstile.getResponse(String(i)); } catch (e) { t = null; }
                            if (t && t.length > 30) return t;
                        }
                    }
                } catch (e) {}
                const inputs = document.querySelectorAll(
                    'input[name="cf-turnstile-response"], input[id$="_response"]'
                );
                for (const el of inputs) {
                    if (el.value && el.value.length > 30) return el.value;
                }
                return null;
            }
            """
        )
    except Exception:
        return None

def _find_visible_cf_frames(page):
    """返回当前可见且有实际尺寸的 Cloudflare 挑战/Turnstile iframe"""
    result = []
    try:
        for handle in page.query_selector_all(CF_IFRAME_SELECTOR):
            try:
                box = handle.bounding_box()
                if box and box['width'] > 10 and box['height'] > 10:
                    result.append(handle)
            except Exception:
                continue
    except Exception:
        pass
    return result

def _cf_checkbox_visible(page):
    """任意 Cloudflare 框架内（含嵌套 iframe）当前是否可见复选框"""
    for frame in page.frames:
        if 'challenges.cloudflare.com' not in (frame.url or ''):
            continue
        try:
            if frame.locator('input[type="checkbox"]:visible').count() > 0:
                return True
        except Exception:
            continue
    return False

def _click_cf_checkbox(page, frame_el=None):
    """点击 Cloudflare/Turnstile 复选框：先试框架内元素，失败退化为人形坐标点击"""
    for frame in page.frames:
        if 'challenges.cloudflare.com' not in (frame.url or ''):
            continue
        try:
            frame.locator('input[type="checkbox"]').first.click(timeout=2500)
            return True
        except Exception:
            continue
    if frame_el is None:
        return False
    try:
        try:
            frame_el.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        box = frame_el.bounding_box()
        if not box:
            return False
        x = box['x'] + random.uniform(18, 34)
        y = box['y'] + box['height'] / 2 + random.uniform(-4, 4)
        page.mouse.move(x - random.uniform(30, 60), y + random.uniform(-8, 8))
        time.sleep(random.uniform(0.15, 0.4))
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.05, 0.2))
        page.mouse.down()
        time.sleep(random.uniform(0.04, 0.1))
        page.mouse.up()
        return True
    except Exception:
        return False

SECURITY_TITLE_HINTS = ('security verification', 'just a moment', 'attention required', 'checking your browser', '请稍候', 'please wait', 'one more step')

def _is_security_check_page(page):
    try:
        title = (page.title() or '').lower()
        if any(h in title for h in SECURITY_TITLE_HINTS):
            return True
    except Exception:
        return True
    try:
        return page.evaluate(
            """
            () => {
                const t = document.body ? document.body.innerText.slice(0, 5000).toLowerCase() : '';
                return t.includes('verify you are human') || t.includes('checking your browser')
                    || t.includes('security verification');
            }
            """
        )
    except Exception:
        return True

def _has_interstitial_iframe(page):
    """是否存在整页挑战 iframe（排除弹窗内 /turnstile/ 组件 iframe）"""
    try:
        for handle in page.query_selector_all(CF_IFRAME_SELECTOR):
            if '/turnstile/' not in (handle.get_attribute('src') or ''):
                return True
    except Exception:
        pass
    return False

def _click_security_submit(page):
    """安全验证页 token 已生成但仍停留时，尝试点击确认/继续按钮"""
    for text in ('Verify', 'Continue', 'Submit', 'Proceed', '验证'):
        try:
            btn = page.locator(f'button:has-text("{text}"):visible, input[type="submit"]:visible').first
            if btn.count() > 0:
                btn.click(timeout=3000)
                log(f"🖱️ 已点击安全验证页的确认按钮（{text}）...")
                return True
        except Exception:
            continue
    return False

def handle_cloudflare(page, timeout=240):
    """整页盾循环（eooce 参数）：超时 240s（>60s 自动 +180s），点击节流 6s，
    通过判定 = 连续 2 轮无挑战。"""
    def challenge_active():
        return _has_interstitial_iframe(page) or _is_security_check_page(page)

    if not challenge_active():
        for _ in range(3):
            time.sleep(1)
            if challenge_active():
                break
        else:
            return True

    log("🔒 检测到 Cloudflare 安全验证...")
    time.sleep(5)
    try:
        page.screenshot(path="security_page.png")
    except Exception:
        pass

    effective_timeout = timeout + (180 if timeout > 60 else 0)
    manual_hinted = False
    start_time = time.time()
    last_click = 0
    clear_rounds = 0
    while time.time() - start_time < effective_timeout:
        if not challenge_active():
            clear_rounds += 1
            if clear_rounds >= 2:
                return True
            time.sleep(1)
            continue
        clear_rounds = 0
        if not manual_hinted and timeout > 60 and time.time() - start_time > timeout - 30:
            log("🤝 自动点击未能通过验证，脚本会继续等待...")
            manual_hinted = True
        if time.time() - last_click > 6:
            frames = _find_visible_cf_frames(page)
            if frames:
                log("🖱️ 点击 Turnstile 验证......")
                if _click_cf_checkbox(page, frames[-1]):
                    last_click = time.time()
                    time.sleep(random.uniform(3, 5))
                    continue
            elif _get_turnstile_token(page) and _is_security_check_page(page):
                if _click_security_submit(page):
                    last_click = time.time()
                    time.sleep(random.uniform(2, 4))
                    continue
        time.sleep(1)
    log("❌ 验证超时。")
    try:
        page.screenshot(path="security_timeout.png")
    except Exception:
        pass
    return False

def solve_modal_turnstile(page, timeout=90):
    """弹窗 Turnstile 启发式（eooce）：token 有→过；点过且复选框消失→过；
    20s 无框且没点过→视为无需验证。"""
    start = time.time()
    last_click = 0
    clicks = 0
    no_frame_seconds = 0
    while time.time() - start < timeout:
        if _get_turnstile_token(page):
            log("✅ Turnstile 验证通过！")
            return True
        frames = _find_visible_cf_frames(page)
        if not frames:
            no_frame_seconds += 1
            if no_frame_seconds >= 20 and clicks == 0:
                log("ℹ️ 未检测到 Turnstile 验证框，无需验证。")
                return True
            time.sleep(1)
            continue
        no_frame_seconds = 0
        if clicks > 0 and time.time() - last_click > 4 and not _cf_checkbox_visible(page):
            time.sleep(2)
            if not _cf_checkbox_visible(page):
                log("✅ Turnstile 复选框已消失，视为验证通过！")
                return True
        if time.time() - last_click > 6 and _cf_checkbox_visible(page):
            log(f"🖱️ 点击 Turnstile 复选框（第 {clicks + 1} 次）...")
            if _click_cf_checkbox(page, frames[-1]):
                clicks += 1
                last_click = time.time()
                time.sleep(random.uniform(3, 5))
                continue
        if clicks == 0 and time.time() - start > 20 and time.time() - last_click > 6:
            if _click_cf_checkbox(page, frames[-1]):
                clicks += 1
                last_click = time.time()
                time.sleep(random.uniform(3, 5))
                continue
        time.sleep(1)
    if _get_turnstile_token(page):
        log("✅ Turnstile 验证通过！")
        return True
    log("❌ Turnstile 验证超时。")
    return False

def open_browser(p):
    """启动浏览器（eooce）：patchright 真 Chrome 有头，viewport 跟随窗口；
    代理 launch 与 context 两级同传；stealth 只在降级分支加。"""
    proxy_arg = {"server": PROXY_SERVER} if IS_PROXY else None
    if USING_PATCHRIGHT:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=['--disable-infobars'],
            proxy=proxy_arg,
        )
        ctx = browser.new_context(viewport=None, proxy=proxy_arg)
        page = ctx.new_page()
        return browser, page
    log("⚠️ 未安装 patchright（建议 pip install patchright），退回原生 playwright，过 Cloudflare 能力较弱")
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars']
    )
    ctx = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent=DEFAULT_UA,
        proxy=proxy_arg,
    )
    page = ctx.new_page()
    page.add_init_script(STEALTH_JS)
    return browser, page

# ================= 登录（session 优先 → Discord 回退） =================

def _is_logged_in(page):
    """登录态判定：同域 + 登录后特征任一可见（Logout / 续期区 / 剩余时间）"""
    try:
        url = page.url or ''
        if 'zero.acorehosting.com' not in url:
            return False
        if _is_security_check_page(page):
            return False
        try:
            if page.locator('a:has-text("Logout"), button:has-text("Logout")').first.count() > 0:
                return True
            if page.locator('div.btn-row').first.count() > 0:
                return True
            if page.locator(REMAINING_SELECTOR).first.count() > 0:
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False

def _inject_session_cookie(page):
    """注入 session Cookie（录制确认名 `session`，域 zero.acorehosting.com）"""
    page.context.add_cookies([{
        'name': 'session',
        'value': SESSION_COOKIE,
        'domain': 'zero.acorehosting.com',
        'path': '/',
        'expires': int(time.time()) + 3600 * 24 * 180,
        'httpOnly': True,
        'secure': True,
        'sameSite': 'Lax'
    }])

def login(page):
    # 1. session Cookie 登录（录制确认，免过盾首选）
    if SESSION_COOKIE:
        log("📇 尝试 session Cookie 登录...")
        try:
            _inject_session_cookie(page)
            page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page, timeout=240)
            time.sleep(2)
            handle_cloudflare(page, timeout=60)
            if _is_logged_in(page):
                log("✅ Cookie 登录成功！")
                return True
            log("❌ Cookie 失效，回退 Discord 登录")
        except Exception as e:
            log(f"⚠️ Cookie 登录尝试异常: {e}")

    # 2. Discord OAuth 回退
    if not DC_EMAIL or not DC_PASSWORD:
        log("❌ Cookie 失效且未配置 Discord 凭证")
        return False
    return _discord_login(page)

def _click_discord_entry(page):
    """点击本站 Discord 登录入口。精确匹配优先，避免误点页脚外链。"""
    candidates = [
        'a[href*="oauth2/authorize"]',
        'a[href*="/oauth"]',
        'button:has-text("Login with Discord")',
        'a:has-text("Login with Discord")',
        'button:has-text("Continue with Discord")',
        'a:has-text("Continue with Discord")',
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            el.wait_for(state="visible", timeout=5000)
            el.click()
            log(f"🖱️ 已点击 Discord 登录入口（{sel}）")
            return True
        except Exception:
            continue
    try:
        el = page.locator('a:has-text("Discord"), button:has-text("Discord")').first
        el.wait_for(state="visible", timeout=5000)
        el.click()
        log("🖱️ 已点击 Discord 登录入口（宽泛匹配）")
        return True
    except Exception:
        return False

def _click_authorize_if_present(page):
    """若停在 Discord 授权确认页，点 Authorize。"""
    try:
        btn = page.locator('button:has-text("Authorize"), button:has-text("Authorise")').first
        if btn.count() == 0:
            return False
        btn.wait_for(state="visible", timeout=8000)
        time.sleep(random.uniform(0.6, 1.2))
        btn.click()
        log("🖱️ 已点击 Discord 授权确认（Authorize）")
        return True
    except Exception:
        return False

def _discord_login(page):
    log("💣 尝试 Discord 登录...")
    try:
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page, timeout=240)
        time.sleep(2)

        if _is_logged_in(page):
            log("✅ 已处于登录状态")
            return True

        log("🔍 查找 Discord 登录入口...")
        _click_discord_entry(page)
        time.sleep(3)
        handle_cloudflare(page, timeout=120)
        time.sleep(2)

        if _is_logged_in(page):
            log("✅ 已处于登录状态（免填表）")
            return True

        log("⌨️ 输入 Discord 账号密码...")
        email_input = page.locator('input[name="email"], input[type="email"], input[inputmode="email"]').first
        pwd_input = page.locator('input[name="password"], input[type="password"]').first
        try:
            email_input.wait_for(state="visible", timeout=30000)
        except Exception:
            handle_cloudflare(page, timeout=120)
            email_input.wait_for(state="visible", timeout=30000)

        human_delay(3, 5)
        email_input.fill(DC_EMAIL, timeout=10000)
        time.sleep(random.uniform(0.6, 1.2))
        pwd_input.fill(DC_PASSWORD, timeout=10000)
        human_delay(3, 5)

        # 注：Discord 侧可能是 hCaptcha（非 CF Turnstile），CF 函数管不到；
        # 若卡验证会超时失败并截图，需人工看 login_fail.png。
        handle_cloudflare(page, timeout=60)

        log("🖱️ 点击登录按钮提交...")
        try:
            page.click('button[type="submit"]', timeout=8000)
        except Exception:
            page.locator('button:has-text("Log In"), button:has-text("登录")').first.click(timeout=10000)
        time.sleep(3)
        handle_cloudflare(page, timeout=240)

        # 120s 导航验收：回到本站且不在 discord 即算回；中途遇授权页则点 Authorize
        nav_start = time.time()
        navigated = False
        while time.time() - nav_start < 120:
            try:
                url = page.url or ''
                if 'discord.com' in url and _click_authorize_if_present(page):
                    time.sleep(2)
                    handle_cloudflare(page, timeout=60)
                    continue
                if 'zero.acorehosting.com' in url and 'discord.com' not in url \
                        and not _is_security_check_page(page):
                    navigated = True
                    break
                if _is_security_check_page(page) or _has_interstitial_iframe(page):
                    handle_cloudflare(page, timeout=60)
            except Exception:
                pass
            time.sleep(1)
        if not navigated:
            log("❌ 登录提交后未能完成跳转。")
            page.screenshot(path="login_fail.png")
            return False

        page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page, timeout=120)
        log(f"📝 当前Title: {page.title()}")
        if not _is_logged_in(page):
            log("❌ 登录失败。")
            page.screenshot(path="login_fail.png")
            return False
        log("✅ Discord 登录成功！")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        try:
            page.screenshot(path="login_fail.png")
        except Exception:
            pass
        return False

# ================= dashboard 读取 =================

def get_renew_href(page):
    """取续期链接 href（/renew?id=<uuid>）。无链接 = 未到时间，返回 None。每次现查。"""
    try:
        el = page.locator(RENEW_LINK_SELECTOR).first
        if el.count() == 0:
            return None
        return el.get_attribute("href")
    except Exception:
        return None

def get_time_remaining(page):
    """获取剩余时间，如 '39h 21m remaining'"""
    try:
        t = page.locator(REMAINING_SELECTOR).first.inner_text(timeout=10000)
        log(f"⏳ 剩余时间: {t.strip()}")
        return t.strip()
    except Exception as e:
        log(f"❌ 获取剩余时间失败: {e}")
        return "未知"

def get_expire_time(page):
    """获取过期时间，如 '2026-10-13 17:59 UTC'"""
    try:
        body_text = page.locator("body").inner_text()
        match = re.search(r'Expires\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC)', body_text, re.IGNORECASE)
        if match:
            expire_time = match.group(1).strip()
            log(f"📅 过期时间: {expire_time}")
            return expire_time
        # 兜底：续期成功页 #resOk 文案 "Server extended to ..."
        m2 = re.search(r'[Ee]xtended to\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC)', body_text)
        if m2:
            expire_time = m2.group(1).strip()
            log(f"📅 过期时间（续期页）: {expire_time}")
            return expire_time
        log("⚠️ 未匹配到 Expires 时间")
    except Exception as e:
        log(f"❌ 获取过期时间失败: {e}")
    return "未知"

# ================= 续期（浏览器：/renew 页自动确认流） =================

def renew_service(page, old_expire):
    """录制流程：dashboard 取 renew 链接 → 进 /renew?id= → 过盾 →
    页 JS 自动 POST confirm → 等 #resOk → 以 Expires 差验收。"""
    try:
        log("➡ 进入续期流程...")
        if 'dashboard' not in (page.url or ''):
            page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        time.sleep(2)

        # 先判链接：无链接（disabled 按钮）= 冷却，直接 NOT_TIME
        renew_href = get_renew_href(page)
        if not renew_href:
            log("⚠️ 无续期链接（按钮 disabled），未到续期时间。")
            try:
                page.screenshot(path="renew_not_allowed.png")
            except Exception:
                pass
            return "NOT_TIME"
        renew_url = renew_href if renew_href.startswith("http") else f"{BASE_URL}{renew_href}"
        log(f"🔗 续期链接: {renew_url}")

        human_delay(2, 4)
        page.goto(renew_url, wait_until="domcontentloaded", timeout=60000)
        # 录制：首次 GET 曾 403（CF 盾中），clearance 后 200
        if not handle_cloudflare(page, timeout=240):
            log("❌ 续期页过盾失败")
            return False
        time.sleep(2)

        # 续期页内嵌 Turnstile（explicit）：录制系自动模式（约 8s 出 token 后页 JS 自动 POST）。
        # 先等 #resOk（最长 60s）；无框出现且复选框可见时才点。
        log("⏳ 等待续期页自动确认（#resOk）...")
        res_ok = page.locator(RESOK_SELECTOR).first
        appeared = False
        start = time.time()
        clicked = False
        while time.time() - start < 90:
            try:
                if res_ok.count() > 0 and res_ok.is_visible():
                    appeared = True
                    break
            except Exception:
                pass
            if not clicked and time.time() - start > 20:
                frames = _find_visible_cf_frames(page)
                if frames and _cf_checkbox_visible(page):
                    log("🖱️ 续期页 Turnstile 需手动点一次...")
                    _click_cf_checkbox(page, frames[-1])
                    clicked = True
            time.sleep(2)
        if not appeared:
            # 兜底：token 有但页没反应时尝试解一次
            if _find_visible_cf_frames(page):
                solve_modal_turnstile(page, timeout=30)
                try:
                    res_ok.wait_for(state="visible", timeout=30000)
                    appeared = True
                except Exception:
                    pass
        if not appeared:
            log("❌ 续期页未出现成功标识（#resOk）。")
            page.screenshot(path="renew_error.png")
            return False

        new_expire = get_expire_time(page)
        log(f"✅ 续期页成功：{new_expire}")
        if old_expire != "未知" and new_expire != "未知" and new_expire == old_expire:
            log("⚠️ 到期时间未变化，续期可能未生效。")
            page.screenshot(path="renew_error.png")
            return False
        return True

    except Exception as e:
        log(f"❌ 续费异常: {e}")
        try:
            page.screenshot(path="renew_error.png")
        except Exception:
            pass
        return False

# ================= 主流程 =================

def main():
    log(f"🔍 凭证检测: SESSION_COOKIE={'已配置' if SESSION_COOKIE else '未配置'}, "
        f"DC_EMAIL={'已配置' if DC_EMAIL else '未配置'}")
    if not SESSION_COOKIE and not (DC_EMAIL and DC_PASSWORD):
        log("❌ 缺少登录凭证（SESSION_COOKIE 或 Discord 账密二选一）")
        sys.exit(1)

    if IS_PROXY:
        log(f"⚙️ 代理已启用: {PROXY_SERVER}")
    else:
        log("🌐 直连模式（未使用代理）")
    current_ip = get_current_ip(PROXY_SERVER)
    log(f"🎯 当前出口IP: {current_ip}")

    # ---- 第一步：HTTP 快捷路径（免浏览器） ----
    if SESSION_COOKIE:
        ok, renew_href, old_expire, old_remaining = http_dashboard_state()
        if ok and renew_href is None:
            log("⏳ 未到续期时间（HTTP 预检），本次免开浏览器")
            send_telegram_notification("⏳ 未到续期时间", old_expire, old_expire, current_ip)
            sys.exit(0)
        if ok and renew_href:
            log(f"📆 续期前：{old_expire}（{old_remaining}）")
            success, new_expire = http_confirm_renew(renew_href)
            if success:
                send_telegram_notification("✅ 续期成功", old_expire, new_expire, current_ip)
                sys.exit(0)
            log("➡ HTTP 快捷路径未走通，转浏览器全流程")

    # ---- 第二步：浏览器全流程 ----
    with sync_playwright() as p:
        browser = None
        try:
            log("🚀 启动反检测内核浏览器...")
            browser, page = open_browser(p)

            if not login(page):
                send_telegram_notification("❌ 登录失败", "未知", "未知", current_ip)
                sys.exit(1)

            old_expire = get_expire_time(page)
            old_remaining = get_time_remaining(page)
            log(f"📆 续期前过期时间：{old_expire}，剩余：{old_remaining}")

            renew_result = renew_service(page, old_expire)

            new_expire = old_expire
            if renew_result == "NOT_TIME":
                log("⏳ 未到续期时间，目前无法续期")
                status = "⏳ 未到续期时间"
            elif renew_result is False:
                log("❌ 续费失败，脚本退出。")
                status = "❌ 续期失败"
            else:
                # 回 dashboard 复核新到期时间
                try:
                    page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
                    handle_cloudflare(page, timeout=120)
                    new_expire = get_expire_time(page)
                except Exception:
                    pass
                log(f"📆 续期后过期时间：{new_expire}")
                status = "✅ 续期成功"

            send_telegram_notification(status, old_expire, new_expire, current_ip)

            if renew_result == "NOT_TIME":
                sys.exit(0)
            elif renew_result is False:
                sys.exit(1)
            else:
                sys.exit(0)
        except SystemExit:
            raise
        except Exception as e:
            log(f"❌ 运行出错: {e}")
            sys.exit(1)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

if __name__ == "__main__":
    main()
