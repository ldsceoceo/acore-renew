## Acore自动续期
使用 Github Actions 自动给 Acore Hosting（AcoreZero）免费服务续期，每次续期 +20 小时。
续期页：`https://zero.acorehosting.com/dashboard`

温馨提示：Acore 登录走 Discord OAuth，建议直接用 `session` Cookie 保活（录制确认），
Cookie 失效时自动回退 Discord Token 免密登录（localStorage 注入，不填密码、不过 hCaptcha）。
尽量使用干净点的节点，否则可能卡 Cloudflare 验证。

## 续期原理（录制校准）

1. HTTP 快捷路径：requests 带 `session` Cookie 读 dashboard，有续期链接（`a.btn.btn-success[href*="/renew"]`）
   则直接 `POST /renew/confirm?id=<uuid>`，返回 `{"ok":true,"expires_at":"..."}` 即成功，全程免开浏览器；
2. 无续期链接（按钮 disabled）= 未到续期时间，直接退出；
3. HTTP 被 CF 拦截或 Cookie 失效时，转浏览器全流程：patchright 真 Chrome 过盾 →
   进 `/renew?id=` 页等页内 Turnstile 自动确认 → 等 `#resOk` 成功标识 → 以 Expires 时间差验收。

## 配置

在仓库 `Settings → Secrets and variables → Actions` 中添加以下 Secrets：

| Secret 名称 | 是否必填 | 说明 | 示例 |
|---|---|---|---|
| `SESSION_COOKIE` | ✅二选一 | `session` Cookie 值（推荐，免过盾） | `eyJ1c2VyX2lkIjo...` |
| `DISCORD_TOKEN`  | ✅二选一 | Discord Token（Cookie 失效时回退，免密登录） | `mfa.abc...` |
| `NODE_LINK`      | ❌可选 | 代理节点地址,例如:vless:// vmess:// trojan:// hysteria2:// anytls:// | |
| `TG_BOT_TOKEN`   | ❌可选 | Telegram Bot Token | |
| `TG_CHAT_ID`     | ❌可选 | Telegram Chat ID | |

`SESSION_COOKIE` 二选一说明：配了它优先走 Cookie（快且免过盾）；不配则每次走 Discord Token 登录。
Cookie 失效时若配了 Token 会自动回退登录，两者都没配则直接报错退出。

`SESSION_COOKIE` 的获取（登录 dashboard 后 F12 或右键检查，选择 应用程序/存储，左边找到 Cookie，
域名 `zero.acorehosting.com` 下复制 `session` 的值）。

`DISCORD_TOKEN` 的获取（登录 discord.com 后 F12，选择 应用程序/存储 → Local Storage →
`https://discord.com` → 复制 `token` 的值，去掉首尾引号）：

> ⚠️ Cookie 和 Token 都含登录态，视同密码，仅填 Secrets，切勿提交到仓库。

### 代理格式（确认在v2rayN里使用正常的节点）

`NODE_LINK` 支持以下任意一种代理协议的完整分享链接（不配置则直连）：

- **VLESS**：`vless://uuid@server:port?security=reality&sni=...&type=ws&...`
- **VMess**：`vmess://base64encoded...`
- **Trojan**：`trojan://password@server:port?sni=...&type=ws&...`
- **tuic**：`tuic://uuid:password@server:port...`
- **anytls**：`anytls://uuid@server:port...`
- **hysteria2**：`hysteria2://base64@server:port...`
- **SOCKS5**：`socks5://user:pass@server:port` 或 `socks://user:pass@server:port`

## 使用

### GitHub Actions 运行步骤

1. Fork 本仓库
2. 在仓库 Secrets 中配置 `SESSION_COOKIE`（或 `DISCORD_TOKEN`），（可选）配置 `TG_BOT_TOKEN`、`TG_CHAT_ID`、`NODE_LINK`
3. Actions 菜单里手动触发 `workflow_dispatch` 先跑一次验证
4. 定时任务已设为每天 UTC 10:10 自动巡检，未到续期时间会自动跳过

### 本地演练

```bash
pip install patchright requests
python -m patchright install --with-deps chrome
set SESSION_COOKIE=你的session值
python app.py
```

---

**⚠️ 免责声明**：本脚本仅供学习交流使用，使用者需遵守 [Acore Hosting](https://acorehosting.com) 的服务条款。因使用本脚本造成的任何问题，作者不承担任何责任。
