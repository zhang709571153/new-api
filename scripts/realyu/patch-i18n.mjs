#!/usr/bin/env node
// Patch zh.json + en.json with RealYu/Claude-Code-focused copy for landing-page
// sections (Hero, Stats, Features, HowItWorks, CTA). Keys remain unchanged so
// upstream English source strings still resolve; only translation values are
// overridden. Idempotent — safe to re-run.
//
// A sentinel key (__realyu_patched_version) is written so future upstream
// merges can detect "this locale carries RealYu overrides" and avoid
// blind overwrites.

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const localesDir = resolve(__dirname, '../../web/default/src/i18n/locales');

const REALYU_PATCH_VERSION = '1.1';

// Map of "English source string (= i18n key)" -> "RealYu Chinese translation".
const zhOverrides = {
  // ───── Hero ─────
  'Unified API Gateway for':
    '为 Claude Code 打造的',
  'All Your AI Models':
    '稳定中转',
  'is an open-source AI API gateway for self-hosted deployments. Connect multiple upstream services, manage models, keys, quotas, logs, and routing policies in one place.':
    '为开发者打造的稳定 Claude Code 中转。1 元 = 1 USD,按量计费,缓存读写透明结算。Anthropic + OpenAI 双协议兼容,零封号风险,国内直连。',
  'Get Started': '免费注册',
  'Go to Dashboard': '进入控制台',
  'View Pricing': '查看价格',

  // ───── Stats ─────
  'upstream services integrated': '上游 Channel',
  'model billing support': 'Claude 模型支持',
  'compatible API routes': '锁定汇率', // renders as "1 RMB = $1 锁定汇率"
  'scheduling controls': '元 起充',

  // ───── Features (top bento) ─────
  'Core Features': '核心特性',
  'Built for developers,': '为 Claude Code 优化',
  'designed for scale': '为生产环境而生',
  'Lightning Fast': '低延迟直连',
  'Optimized network architecture ensures millisecond response times':
    '国内 CN2 + 多上游热切换,避开公网拥堵,毫秒级响应',
  'Secure & Reliable': '稳定不封号',
  'Enterprise-grade security with comprehensive permission management':
    '官方 API 反代 + token 全程加密,零账号风险',
  'Global Coverage': '全模型覆盖',
  'Multi-region deployment for stable global access':
    'Claude Opus / Sonnet / Haiku + 缓存读写透明计费',
  'Developer Friendly': 'Claude Code 即插即用',
  'Compatible API routes for common AI application workflows':
    '直接 export ANTHROPIC_BASE_URL,零改造接入 Cline / Cursor / opencode',
  'Multi-protocol Compatible': '双协议兼容',

  // ───── Features (bottom row) ─────
  'High Performance': '透明计费',
  'Support for high concurrency with automatic load balancing':
    '每次调用 token 数 + 缓存读写实时显示,按官方价 1:1',
  'Transparent Billing': '极速接入',
  'Pay-as-you-go with real-time usage monitoring':
    '注册后 1 分钟内拿到 sk-key,配置 base URL 即用',
  'Team Collaboration': '弹性扩容',
  'Multi-user management with flexible permission allocation':
    '多上游自动调度,单家断流不影响整体可用',
  'Open Source': 'AGPL 开源',
  'Community driven, self-hosted, and extensible':
    '基于 NewAPI 二次开发,源码可索取',

  // ───── How It Works ─────
  'How It Works': '一分钟接入',
  'Three steps to get started': '三步即可开始',
  Configure: '注册账号',
  'Add your API keys, set up channels and configure access permissions':
    '邮箱 + 密码即可注册,无需邮箱验证,无需邀请码',
  Connect: '创建密钥',
  'Connect through OpenAI, Claude, Gemini, and other compatible API routes':
    '控制台 → API Keys → 一键创建 sk-token,按需限额',
  Monitor: '配置使用',
  'Track usage, costs and performance with real-time analytics':
    'export ANTHROPIC_BASE_URL=https://api.realyu.fun,开始使用',

  // ───── CTA ─────
  'Ready to simplify': '准备好接入',
  'your AI integration?': '稳定的 Claude API 了吗?',
  'Deploy your own gateway and start routing requests through your configured upstream services.':
    '免费注册,立即创建你的第一个 token。1 元 = 1 USD,最低充值 ¥10。',
};

// English mirrors so the locale switcher does not fall back to raw
// upstream-NewAPI marketing copy for our forked sections.
const enOverrides = {
  'Unified API Gateway for': 'A reliable Claude Code',
  'All Your AI Models': 'API relay',
  'is an open-source AI API gateway for self-hosted deployments. Connect multiple upstream services, manage models, keys, quotas, logs, and routing policies in one place.':
    'Built for developers who need a steady Claude Code endpoint. Pay-as-you-go, transparent cache billing, ¥1 = $1 USD, dual Anthropic + OpenAI protocols.',
  'Get Started': 'Sign up free',
  'Go to Dashboard': 'Open dashboard',
  'View Pricing': 'View pricing',

  'upstream services integrated': 'upstream channel',
  'model billing support': 'Claude model',
  'compatible API routes': 'locked rate', // "1 RMB = $1 locked rate"
  'scheduling controls': '¥ minimum top-up',

  'Core Features': 'Core features',
  'Built for developers,': 'Optimized for Claude Code,',
  'designed for scale': 'built for production',
  'Lightning Fast': 'Low latency',
  'Optimized network architecture ensures millisecond response times':
    'CN2 backbone + multi-upstream hot-swap; sub-second response',
  'Secure & Reliable': 'No bans, no leaks',
  'Enterprise-grade security with comprehensive permission management':
    'Official API reverse proxy + end-to-end token encryption',
  'Global Coverage': 'Full Claude lineup',
  'Multi-region deployment for stable global access':
    'Opus / Sonnet / Haiku + transparent cache read/write billing',
  'Developer Friendly': 'Plug-and-play with Claude Code',
  'Compatible API routes for common AI application workflows':
    'Just export ANTHROPIC_BASE_URL — works with Cline / Cursor / opencode out of the box',
  'Multi-protocol Compatible': 'Dual-protocol',

  'High Performance': 'Transparent billing',
  'Support for high concurrency with automatic load balancing':
    'Per-call tokens + cache read/write surfaced in real time, billed 1:1 against official rates',
  'Transparent Billing': 'Instant onboarding',
  'Pay-as-you-go with real-time usage monitoring':
    'Mint an sk-key in under a minute; set the base URL and you are live',
  'Team Collaboration': 'Elastic capacity',
  'Multi-user management with flexible permission allocation':
    'Multi-upstream auto-routing — a single source going dark does not take you offline',
  'Open Source': 'AGPL open source',
  'Community driven, self-hosted, and extensible':
    'Forked from NewAPI; modified source available on request',

  'How It Works': 'Get started in a minute',
  'Three steps to get started': 'Three steps from zero to first call',
  Configure: 'Sign up',
  'Add your API keys, set up channels and configure access permissions':
    'Email + password. No email verification, no invite code.',
  Connect: 'Create a key',
  'Connect through OpenAI, Claude, Gemini, and other compatible API routes':
    'Console → API Keys → mint an sk-token, set per-key quota',
  Monitor: 'Configure & use',
  'Track usage, costs and performance with real-time analytics':
    'export ANTHROPIC_BASE_URL=https://api.realyu.fun and you are live',

  'Ready to simplify': 'Ready to plug in',
  'your AI integration?': 'a reliable Claude API?',
  'Deploy your own gateway and start routing requests through your configured upstream services.':
    'Sign up free and mint your first token. ¥1 = $1 USD, minimum top-up ¥10.',
};

function patchLocale(filename, overrides) {
  const filePath = resolve(localesDir, filename);
  const data = JSON.parse(readFileSync(filePath, 'utf-8'));
  const t = data.translation;

  let updated = 0;
  let added = 0;
  for (const [key, value] of Object.entries(overrides)) {
    if (key in t) {
      if (t[key] !== value) {
        t[key] = value;
        updated++;
      }
    } else {
      t[key] = value;
      added++;
    }
  }

  // Sentinel: lets future upstream-merge tooling detect a RealYu-patched locale.
  t.__realyu_patched_version = REALYU_PATCH_VERSION;

  writeFileSync(filePath, JSON.stringify(data, null, 4) + '\n', 'utf-8');
  console.log(
    `${filename}: ${updated} updated, ${added} added (sentinel=${REALYU_PATCH_VERSION})`
  );
}

patchLocale('zh.json', zhOverrides);
patchLocale('en.json', enOverrides);
