#!/usr/bin/env node
// One-shot: set RealYu API content + toggles via NewAPI's option API.

const BASE  = 'http://127.0.0.1:3000';
const TOKEN = process.env.NEW_API_TOKEN;
if (!TOKEN) { console.error('NEW_API_TOKEN env var required'); process.exit(1); }
const HDR = { 'Authorization': TOKEN, 'New-Api-User': '1', 'Content-Type': 'application/json' };

const HOMEPAGE = `# RealYu API

为开发者打造的稳定 Claude Code 中转。1 元 = 1 USD，按量计费，缓存读写透明结算。

## 当前可用模型

- ✅ **claude-opus-4-6** （200K context · 输入 \\$15/1M · 输出 \\$75/1M）
- 🚧 claude-opus-4-7 / claude-sonnet-4-6 / claude-haiku-4-5 — 开发中
- 🚧 GPT / Gemini 系列 — 开发中

## 一分钟接入 Claude Code

1. **注册**账号（邮箱密码即可，无需验证）
2. 控制台 → **API Keys** → 创建一个 token
3. 终端：

\`\`\`bash
export ANTHROPIC_AUTH_TOKEN=sk-...
export ANTHROPIC_BASE_URL=https://api.realyu.fun
claude
\`\`\`

完成。

## 几个关键设计

- **按量计费 · 透明账单** — 每一次调用的 token 数 + 缓存读/写都在控制台逐条显示
- **OpenAI / Anthropic 双协议** — \`/v1/messages\` 走 Anthropic、\`/v1/chat/completions\` 走 OpenAI
- **本地多上游** — 自动切换可用 channel，单个上游故障不影响整体

## 开始使用

[注册](/register) · [价格](/pricing) · [文档](/about) · [API Keys](/keys)
`;

const FOOTER = `<div style="text-align:center; font-size:13px; line-height:1.7; color:#888; padding:12px 0;">
  © 2026 RealYu API · <a href="/about" style="color:inherit; text-decoration:underline;">文档</a> · <a href="mailto:hi@realyu.fun" style="color:inherit; text-decoration:underline;">联系</a> · <a href="https://github.com/Calcium-Ion/new-api" style="color:inherit; text-decoration:underline;" target="_blank" rel="noopener">基于 NewAPI (AGPL v3.0) · 修改源码可在请求时索取</a>
</div>`;

const ABOUT = `# RealYu API 文档

## Base URL

- **Anthropic-compatible**: \`https://api.realyu.fun\`
- **OpenAI-compatible**: \`https://api.realyu.fun/v1\`

## 鉴权

所有请求带 \`Authorization: Bearer sk-<your-token>\` header。

token 在 [API Keys](/keys) 创建。

## 例子（Anthropic Messages API）

\`\`\`bash
curl -X POST https://api.realyu.fun/v1/messages \\
  -H "Authorization: Bearer sk-..." \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "claude-opus-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
\`\`\`

## Claude Code 配置

\`\`\`bash
export ANTHROPIC_AUTH_TOKEN=sk-...
export ANTHROPIC_BASE_URL=https://api.realyu.fun
claude
\`\`\`

或写到 \`~/.claude/settings.json\`：

\`\`\`json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "sk-...",
    "ANTHROPIC_BASE_URL": "https://api.realyu.fun"
  }
}
\`\`\`

## Cline / Cursor / opencode 配置

- API Provider 选 **Anthropic**
- API Key: 你的 \`sk-...\`
- Custom URL / Base URL: \`https://api.realyu.fun\`

## 计费规则

- 按官方 Anthropic 价格 1:1
- 内部汇率：**1 元 = 1 USD**，最低充值 ¥10
- **缓存读 / 缓存写**单独按 Anthropic 标准比例计费（输入价的 0.1× / 1.25×）
- 每次调用的 token 数和费用在 [使用日志](/usage-logs/common) 逐条可查

## 当前模型清单

| 模型 ID | 输入 ($/1M) | 输出 ($/1M) | 状态 |
|---|---|---|---|
| claude-opus-4-6 | 15.00 | 75.00 | ✅ 可用 |
| claude-opus-4-7 | 15.00 | 75.00 | ✅ 可用 |
| claude-sonnet-4-6 | 3.00 | 15.00 | ✅ 可用 |
| claude-haiku-4-5 | 1.00 | 5.00 | ⚠️ 上游间歇性额度不足 |

## 开源声明

本服务基于 [Calcium-Ion/new-api](https://github.com/Calcium-Ion/new-api) 修改部署，遵循 AGPL v3.0 协议。修改后的源代码可在请求时通过 [hi@realyu.fun](mailto:hi@realyu.fun) 索取。
`;

// Mirror of DEFAULT_SIDEBAR_MODULES from web/default/src/hooks/use-sidebar-config.ts.
// Writing this to DB locks the hide-state so an admin UI save can't accidentally
// re-enable hidden modules (the in-code defaults only apply when DB value is empty).
const SIDEBAR_MODULES = {
  chat: { enabled: false, playground: false, chat: false },
  console: { enabled: true, detail: true, token: true, log: true, midjourney: false, task: false },
  personal: { enabled: true, topup: true, personal: true },
  admin: { enabled: true, channel: true, models: true, redemption: true, user: true, setting: true, subscription: false },
};

const updates = [
  { key: 'theme.frontend',        value: 'default' },
  { key: 'SystemName',            value: 'RealYu API' },
  // Push empty string so the React Hero / Stats / Features / HowItWorks / CTA
  // render. Setting any non-empty value triggers Markdown-only rendering in
  // web/default/src/features/home/index.tsx and we lose the rich landing UX.
  // (HOMEPAGE constant kept above for reference / future revival.)
  { key: 'HomePageContent',       value: '' },
  { key: 'Footer',                value: FOOTER },
  { key: 'About',                 value: ABOUT },
  { key: 'SidebarModulesAdmin',   value: JSON.stringify(SIDEBAR_MODULES) },
  { key: 'console_setting.announcements_enabled', value: 'false' },
  { key: 'console_setting.faq_enabled',           value: 'false' },
  { key: 'console_setting.uptime_kuma_enabled',   value: 'false' },
  // Self-use mode bypasses NewAPI's "model price not configured" 400 error.
  // Required while we expose multiple Claude models without per-model price
  // entries in the ModelPrice / ModelRatio config. V2: configure ratios and
  // set this back to false for commercial billing.
  { key: 'SelfUseModeEnabled',    value: 'true' },
];

(async () => {
  for (const u of updates) {
    const r = await fetch(BASE + '/api/option/', {
      method: 'PUT',
      headers: HDR,
      body: JSON.stringify(u),
    });
    const text = await r.text();
    let body; try { body = JSON.parse(text); } catch { body = { raw: text.slice(0, 200) }; }
    const ok = body?.success === true;
    console.log(`[${ok ? 'OK ' : 'ERR'}] ${u.key.padEnd(40)} http=${r.status} ${ok ? `(value len=${u.value.length})` : 'msg=' + (body?.message || '?').slice(0, 80)}`);
  }
})();
