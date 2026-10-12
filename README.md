## 配置

在仓库 `Settings → Secrets and variables → Actions` 中添加以下 Secrets：

| Secret 名称 | 是否必填 | 说明 | 示例 |
|---|---|---|---|
| `SESSION_COOKIE` | ✅二选一 | `session` Cookie 值（推荐，免过盾） | `eyJ1c2VyX2lkIjo...` |
| `DISCORD_TOKEN`  | ✅二选一 | Discord Token（Cookie 失效时回退，免密登录） | `mfa.abc...` |
| `GH_TOKEN`       | ❌可选 | 有 secrets 写权限的 PAT，用于 Cookie 失效后自动写回新 `SESSION_COOKIE` | |
| `NODE_LINK`      | ❌可选 | 代理节点地址,例如:vless:// vmess:// trojan:// hysteria2:// anytls:// | |
| `TG_BOT_TOKEN`   | ❌可选 | Telegram Bot Token | |
| `TG_CHAT_ID`     | ❌可选 | Telegram Chat ID | |
