// Fresh headless browser against the reviewed isolated UX candidate only.
// No existing browser profile, production origin, real customer or model call.
// Default is offline; evidence paths are unique and retain the first failure.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const ROOT = 'C:/srv/realyu-singlecore-dev/runtime';
const BASE = 'http://127.0.0.1:29481';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const PYTHON = path.join(ROOT, 'venv/Scripts/python.exe');
const args = process.argv.slice(2);
const option = key => { const i = args.indexOf(key); return i < 0 ? undefined : args[i + 1]; };
const db = (mode = 'preflight', user, planID) => JSON.parse(execFileSync(PYTHON,
  [path.join(ROOT, 'ux-browser-fixture-ops.py'), '--isolated-confirmed', '--mode', mode,
    ...(user ? ['--login', user.login, '--user-id', String(user.id)] : []),
    ...(planID ? ['--plan-id', String(planID)] : [])],
  { encoding: 'utf8', windowsHide: true, timeout: 15000 }));
const ownerFunds = value => ({ owner_wallet_usd: value.owner_wallet_usd,
  member_cap_status: value.member_state.slice(0, 2), member_period_usage: value.member_period_usage,
  owner_subscription: value.owner_subscription });

async function main() {
  if (!args.includes('--candidate-ready')) {
    console.log(JSON.stringify({ status: 'PREPARED_NOT_RUN', target: BASE, network_accessed: false,
      fixture_read: false, requires: '--candidate-ready --expected-version <reviewed exact version>' }));
    return;
  }
  const expectedVersion = option('--expected-version');
  assert.equal(expectedVersion, 'realyu-singlecore-v0.2.15-20261010-ux', 'Reviewed UX build version required');
  const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'team-http-fixture.private.json'), 'utf8'));
  assert.equal(fixture.target, 'http://127.0.0.1:29480'); // Same isolated DB, new candidate port.
  assert.equal(fixture.database, 'realyu_singlecore_e2e');
  assert.equal(fixture.prefix, 'team-e2e-20261010');
  for (const role of ['admin', 'owner', 'member', 'role10']) assert.equal(fixture.users[role].login_name, fixture.prefix + '-' + role);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const runID = crypto.randomBytes(7).toString('hex');
  const testUser = { login: 'ux-e2e-20261010-' + runID, password: 'UXe2e!' + crypto.randomBytes(18).toString('hex'), id: 0 };
  const out = path.join(ROOT, 'screenshots', 'ux-parity-private-' + stamp);
  fs.mkdirSync(out, { recursive: true });
  const reportPath = path.join('C:/srv/realyu-singlecore-dev/results', 'ux-parity-browser-' + stamp + '.json');
  const result = { target: BASE, expected_version: expectedVersion, database: fixture.database, synthetic_only: true,
    production_modified: false, existing_browser_profiles_accessed: false, paid_model_called: false,
    clipboard: 'browser-memory adapter; OS clipboard not accessed', started_at: new Date().toISOString(),
    checks: [], page_errors: [], network_failures: [], blocked_requests: [], screenshots: [], fixture_ids: {} };
  const secrets = [fixture.password, testUser.password, ...Object.values(fixture.keys || {}).map(x => x.key).filter(Boolean)];
  const safe = value => { let text = String(value); for (const secret of secrets) if (secret) text = text.split(secret).join('[REDACTED]');
    return text.replace(/Bearer\s+[^\s"']+/gi, 'Bearer [REDACTED]').replace(/sk-[A-Za-z0-9_-]+/g, '[REDACTED]').slice(0, 1800); };
  const save = () => fs.writeFileSync(path.join(out, 'result.private.json'), JSON.stringify(result, null, 2));
  const record = (name, pass, detail) => { result.checks.push({ name, pass, ...(detail ? { detail: safe(detail) } : {}) }); save(); };
  const check = async (name, fn) => { try { await fn(); record(name, true); return true; }
    catch (error) { record(name, false, error.stack || error.message); return false; } };
  const json = async response => { assert.ok([200, 201].includes(response.status()), 'HTTP success status expected, received ' + response.status()); const body = await response.json();
    assert.ok(body.code === undefined || body.code === 0, 'Native API must return success code'); return body.data ?? body; };
  let baseline, browser, admin, owner, member, registered, originalSettings, settingsTouched = false, originalPaymentEnabled;
  let planID = 0, nicknameTouched = false, extraOwnerKeyID = 0;
  const teamPath = `/api/v1/realyu/teams/${fixture.teams.a}`;
  const memberPath = `${teamPath}/members/${fixture.users.member.id}`;
  const readonlyPosts = new Set(['/api/v1/admin/accounts/usage/batch', '/api/v1/admin/accounts/today-stats/batch',
    '/api/v1/admin/dashboard/users-usage', '/api/v1/admin/dashboard/api-keys-usage', '/api/v1/admin/user-attributes/batch',
    '/api/v1/usage/dashboard/api-keys-usage', '/api/v1/admin/payment/managed-subscriptions/query']);
  const screenshot = async (session, label) => {
    if (['/login', '/register'].includes(new URL(session.page.url()).pathname)) return;
    const file = path.join(out, label + '.png');
    await session.page.screenshot({ path: file, fullPage: true, animations: 'disabled',
      mask: [session.page.locator('[data-testid="setup-command"]'), session.page.locator('code')] });
    result.screenshots.push(file);
  };
  const newSession = async role => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai', serviceWorkers: 'block' });
    await context.addInitScript(() => {
      window.__uxClipboard = [];
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
        writeText: async value => { window.__uxClipboard.push(String(value)); }
      } });
    });
    const page = await context.newPage();
    page.setDefaultTimeout(22000);
    const session = { context, page, role, token: '', userID: 0 };
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), method = request.method();
      if (url.origin !== BASE) { result.blocked_requests.push({ role, host: url.hostname, reason: 'outside_candidate' }); return route.abort('blockedbyclient'); }
      const read = ['GET', 'HEAD', 'OPTIONS'].includes(method);
      const auth = method === 'POST' && ['/api/v1/auth/login', '/api/v1/auth/refresh'].includes(url.pathname);
      const register = role === 'registered' && method === 'POST' && url.pathname === '/api/v1/auth/register';
      const nickname = role === 'owner' && method === 'PUT' && url.pathname === memberPath;
      const plans = role === 'admin' && ((method === 'POST' && url.pathname === '/api/v1/admin/payment/plans')
        || (method === 'PUT' && /^\/api\/v1\/admin\/payment\/plans\/\d+(?:\/managed-entitlement)?$/.test(url.pathname)));
      const purchase = role === 'registered' && method === 'POST' && url.pathname === '/api/v1/payment/orders';
      if (!read && !auth && !register && !nickname && !plans && !purchase && !(method === 'POST' && readonlyPosts.has(url.pathname))) {
        result.blocked_requests.push({ role, path: url.pathname, reason: 'unexpected_write_blocked' }); return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    page.on('pageerror', error => result.page_errors.push({ role, error: safe(error.message) }));
    page.on('response', response => {
      const url = new URL(response.url());
      if (url.origin === BASE && response.status() >= 400) result.network_failures.push({ role, path: url.pathname, status: response.status() });
    });
    session.api = (method, endpoint, data) => {
      assert.ok(endpoint.startsWith('/api/v1/') && !endpoint.includes('://'), 'API limited to native candidate');
      return context.request.fetch(BASE + endpoint, { method, ...(data === undefined ? {} : { data }),
        headers: session.token ? { Authorization: 'Bearer ' + session.token } : {}, timeout: 20000 });
    };
    session.navigate = async (pathname, endpoint) => {
      const wait = endpoint ? page.waitForResponse(r => new URL(r.url()).pathname === endpoint && r.request().method() === 'GET') : null;
      await page.goto(BASE + pathname, { waitUntil: 'domcontentloaded' });
      if (wait) await json(await wait);
      await page.locator('main').first().waitFor({ state: 'visible' });
      assert.equal(await page.locator('#admin-compliance-phrase').count(), 0, 'First-use confirmation must be absent');
    };
    return session;
  };
  const login = async role => {
    const session = await newSession(role), { page } = session;
    await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
    await page.locator('#email').fill(fixture.users[role].login_name);
    await page.locator('#password').fill(fixture.password);
    const wait = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/auth/login' && r.request().method() === 'POST');
    await page.locator('button[type="submit"]').click();
    const data = await json(await wait); session.token = data.access_token; secrets.push(session.token);
    assert.equal(data.user.id, fixture.users[role].id); session.userID = data.user.id;
    await page.waitForURL(url => url.pathname !== '/login');
    await page.locator('main').first().waitFor({ state: 'visible' });
    assert.equal(await page.locator('#admin-compliance-phrase').count(), 0);
    return session;
  };
  const setupCheck = async (session, scope) => {
    const { page } = session;
    await session.navigate(session.role === 'admin' ? '/admin/dashboard' : '/dashboard');
    await page.getByTestId('workspace-setup').waitFor({ state: 'visible' });
    if (scope === 'team') await page.getByTestId('setup-scope-team').click();
    await page.getByTestId('setup-key').waitFor({ state: 'visible' });
    await page.getByTestId('setup-copy').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.querySelector('[data-testid="setup-copy"]')?.disabled);
    assert.ok(!/sk-[A-Za-z0-9_-]{16,}/.test(await page.getByTestId('setup-command').inputValue()), 'Initial command must be masked');
    const selectedID = Number(await page.getByTestId('setup-key').inputValue());
    const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/v1/keys/${selectedID}` && r.request().method() === 'GET');
    await page.getByTestId('setup-copy').click();
    const detail = await json(await response); secrets.push(detail.key);
    assert.equal(detail.user_id, session.userID); assert.equal(detail.realyu_scope.kind, scope); assert.equal(detail.realyu_scope.active, true);
    await page.waitForFunction(() => window.__uxClipboard.length > 0);
    const command = await page.evaluate(() => window.__uxClipboard.at(-1));
    assert.ok(command.includes(detail.key) && command.includes('https://api.realyu.fun/downloads/realyu/setup.ps1?v=2'), 'Copy must use selected key and published installer');
    assert.ok(!(await page.getByTestId('setup-command').inputValue()).includes(detail.key), 'Copy must not reveal key');
    assert.equal(await page.evaluate(key => [localStorage, sessionStorage].some(storage => Object.values(storage).some(value => String(value).includes(key))), detail.key), false, 'Setup key must not be persisted');
    await page.getByTestId('setup-reveal').click();
    await page.waitForFunction(key => document.querySelector('[data-testid="setup-command"]')?.value.includes(key), detail.key);
    await page.getByTestId('setup-client').selectOption('workbuddy');
    assert.ok(!(await page.getByTestId('setup-command').inputValue()).includes(detail.key), 'Client switch must clear key');
    await page.getByTestId('setup-platform').selectOption('unix');
    const before = await page.evaluate(() => window.__uxClipboard.length);
    await page.getByTestId('setup-copy').click();
    await page.waitForFunction(count => window.__uxClipboard.length > count, before);
    const workbuddy = await page.evaluate(() => window.__uxClipboard.at(-1));
    assert.ok(workbuddy.includes(detail.key) && workbuddy.includes('setup-workbuddy.sh?v=1'), 'macOS WorkBuddy must reuse original installer');
    await page.evaluate(() => { window.__uxClipboard = []; });
    await screenshot(session, session.role + '-setup-' + scope);
  };
  try {
    const candidate = JSON.parse(fs.readFileSync(path.join(ROOT, 'ux-candidate-process.json'), 'utf8').replace(/^\uFEFF/, ''));
    assert.equal(candidate.port, 29481); assert.equal(candidate.database, fixture.database);
    assert.ok(Number.isSafeInteger(candidate.pid) && candidate.pid > 0);
    const digest = crypto.createHash('sha256').update(fs.readFileSync(candidate.executable)).digest('hex');
    assert.equal(digest.toLowerCase(), candidate.sha256.toLowerCase(), 'Candidate binary pin must match reviewed receipt');
    result.candidate_binary_sha256 = digest; result.candidate_pid = candidate.pid;
    baseline = db();
    const health = await fetch(BASE + '/api/status', { signal: AbortSignal.timeout(10000) });
    assert.equal(health.status, 200); const state = await health.json();
    assert.equal(state.data.version, expectedVersion); assert.equal(state.data.single_core, true);
    const publicResponse = await fetch(BASE + '/api/v1/settings/public', { signal: AbortSignal.timeout(10000) });
    assert.equal(publicResponse.status, 200); const settings = (await publicResponse.json()).data;
    assert.equal(settings.customer_currency, 'CNY'); assert.equal(Number(settings.customer_usd_to_cny), 7);
    assert.equal(new URL(settings.site_logo, BASE).pathname, '/brand/realyu-wordmark.png');
    result.baseline_synthetic_snapshot = baseline;
    record('exact-isolated-version-brand-cny7-and-disabled-upstream', true);
    const { chromium } = require('C:/Users/chunhei/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
    browser = await chromium.launch({ executablePath: EDGE, headless: true });
    result.browser_version = browser.version();

    const adminOK = await check('admin-native-pages-no-confirmation-workspace-brand', async () => {
      admin = await login('admin'); const sidebar = admin.page.locator('aside.sidebar');
      await sidebar.waitFor({ state: 'visible' });
      assert.equal(await sidebar.locator('.sidebar-brand button').count(), 0);
      const workspaceLabel = (await sidebar.locator('.sidebar-nav a[href="/admin/dashboard"]').first().innerText()).trim();
      assert.ok(['工作台', 'Workspace'].includes(workspaceLabel), 'Workspace label: ' + workspaceLabel);
      assert.equal(await sidebar.locator('a[href="/teams"]').count(), 1);
      for (const href of ['/redeem', '/admin/redeem', '/admin/promo-codes']) assert.equal(await sidebar.locator(`a[href="${href}"]`).count(), 0);
      for (const [route, endpoint] of [['/admin/dashboard', '/api/v1/admin/dashboard/snapshot-v2'],
        ['/admin/accounts', '/api/v1/admin/accounts'], ['/admin/usage', '/api/v1/admin/usage'], ['/admin/users', '/api/v1/admin/users']]) {
        await admin.navigate(route, endpoint); await screenshot(admin, 'admin-' + route.split('/').at(-1));
      }
    });

    await check('owner-personal-team-setup-and-server-key-scope-pagination', async () => {
      owner = await login('owner');
      let own = await json(await owner.api('GET', '/api/v1/keys?page=1&page_size=100&realyu_scope=personal'));
      if (!own.items.some(k => k.realyu_scope?.active && ['active', 'quota_exhausted'].includes(k.status))) {
        const key = await json(await owner.api('POST', '/api/v1/keys', { name: testUser.login + '-owner-personal', group_id: fixture.group_id }));
        secrets.push(key.key); extraOwnerKeyID = key.id;
        assert.equal(key.user_id, owner.userID);
        const detail = await json(await owner.api('GET', `/api/v1/keys/${key.id}`)); secrets.push(detail.key);
        assert.equal(detail.realyu_scope.kind, 'personal');
      }
      await setupCheck(owner, 'personal'); await setupCheck(owner, 'team');
      await owner.navigate('/keys', '/api/v1/keys');
      for (const scope of ['personal', 'team', 'all']) {
        const response = owner.page.waitForResponse(r => { const u = new URL(r.url()); return u.pathname === '/api/v1/keys'
          && r.request().method() === 'GET' && (scope === 'all' ? !u.searchParams.has('realyu_scope') : u.searchParams.get('realyu_scope') === scope); });
        await owner.page.getByTestId('keys-scope-' + scope).click();
        const listed = await json(await response);
        for (const key of listed.items) { secrets.push(key.key); assert.equal(key.user_id, owner.userID); if (scope !== 'all') assert.equal(key.realyu_scope.kind, scope); }
        assert.equal(listed.items.length, Math.min(listed.total, listed.page_size));
        if (listed.items.length) {
          await owner.page.locator(`table tbody tr[data-row-id="${listed.items[0].id}"]`).waitFor({ state: 'visible' });
          assert.equal(await owner.page.locator('table tbody tr[data-row-id]').count(), listed.items.length);
          const info = owner.page.locator('nav[aria-label="Pagination"]').locator('..').locator('p').first();
          assert.ok((await info.innerText()).includes(String(listed.total)), 'UI total must match server scope total');
        }
      }
      await screenshot(owner, 'owner-keys-scopes');
    });

    await check('owner-team-nickname-cumulative-sorting-with-funds-unchanged', async () => {
      assert.ok(owner, 'Owner session required');
      await owner.navigate('/teams', '/api/v1/realyu/teams');
      await owner.page.getByTestId('team-select').selectOption(String(fixture.teams.a));
      await owner.page.getByTestId('member-' + fixture.users.member.id).waitFor({ state: 'visible' });
      let overview = await json(await owner.api('GET', teamPath));
      assert.equal(overview.team.can_manage, true);
      for (const metric of ['cumulative_cost_usd', 'cumulative_tokens']) {
        assert.ok(overview.members.every(m => m[metric] !== undefined), 'Cumulative team fields required');
        await owner.page.getByTestId('sort-member-' + metric).click();
        const expected = [...overview.members].sort((a, b) => Number(b[metric]) - Number(a[metric]) || a.user_id - b.user_id).map(x => x.user_id);
        assert.deepEqual(await owner.page.locator('tr[data-testid^="member-"]').evaluateAll(rows => rows.map(row => Number(row.dataset.testid.slice(7)))), expected);
      }
      await owner.page.getByTestId('edit-nickname-' + fixture.users.member.id).click();
      await owner.page.getByTestId('nickname-form').locator('input').fill('测试别名-' + runID);
      const change = owner.page.waitForResponse(r => new URL(r.url()).pathname === memberPath && r.request().method() === 'PUT');
      nicknameTouched = true;
      await owner.page.getByTestId('nickname-form').locator('button[type="submit"]').click();
      await json(await change);
      overview = await json(await owner.api('GET', teamPath));
      assert.equal(overview.members.find(m => m.user_id === fixture.users.member.id).nickname, '测试别名-' + runID);
      assert.deepEqual(ownerFunds(db()), ownerFunds(baseline));
      await screenshot(owner, 'owner-team-nickname-sort');
    });

    await check('member-own-team-usage-and-server-forbidden-boundaries', async () => {
      member = await login('member');
      await member.navigate('/teams', '/api/v1/realyu/teams');
      await member.page.getByTestId('team-select').selectOption(String(fixture.teams.a));
      await member.page.getByTestId('usage-items').waitFor({ state: 'visible' });
      assert.equal(await member.page.locator('[data-testid^="edit-nickname-"]').count(), 0);
      assert.equal(await member.page.getByTestId('member-filter').count(), 0);
      const details = await member.page.getByTestId('usage-items').locator('tbody tr').allTextContents();
      assert.ok(details.length > 0 && details.every(text => text.includes('#' + fixture.users.member.id)));
      if (nicknameTouched) assert.ok(details.some(text => text.includes('测试别名-' + runID)));
      for (const [method, endpoint, body] of [['GET', '/api/v1/admin/users'], ['GET', `/api/v1/realyu/teams/${fixture.teams.b}`],
        ['PUT', memberPath, { nickname: 'FORBIDDEN-DO-NOT-WRITE' }], ['PUT', memberPath, { status: 'active', weekly_cap_quota: 900000000 }]]) {
        const response = await member.api(method, endpoint, body); assert.equal(response.status(), 403, 'Member privilege escalation must be forbidden');
      }
      const role10 = await login('role10');
      assert.equal((await role10.api('GET', '/api/v1/admin/users')).status(), 403);
      await role10.context.close();
      await screenshot(member, 'member-own-usage-no-admin');
    });

    if (admin?.token) await check('registration-without-email-default-personal-key-zero-grants', async () => {
      let before = await json(await admin.api('GET', '/api/v1/admin/settings'));
      if (!Object.hasOwn(before, 'realyu_signup_personal_group_id')) {
        // Root explicitly authorized group 3 as the isolated baseline when absent.
        // This is not a production change and null cannot delete this native setting.
        fs.writeFileSync(path.join(out, 'signup-group-before.private.json'), JSON.stringify({ present: false }, null, 2), { flag: 'wx' });
        await json(await admin.api('PUT', '/api/v1/admin/settings', { realyu_signup_personal_group_id: fixture.group_id }));
        before = await json(await admin.api('GET', '/api/v1/admin/settings'));
        assert.equal(before.realyu_signup_personal_group_id, fixture.group_id);
        fs.writeFileSync(path.join(out, 'signup-group-baseline.private.json'), JSON.stringify({ present: true, group_id: fixture.group_id }, null, 2), { flag: 'wx' });
        record('authorized-isolated-registration-group-baseline-created', true);
      }
      const desired = { registration_enabled: true, realyu_username_registration_enabled: true,
        realyu_signup_personal_group_id: fixture.group_id, email_verify_enabled: false, invitation_code_enabled: false,
        promo_code_enabled: false, turnstile_enabled: false, tencent_captcha_enabled: false, aliyun_captcha_enabled: false,
        login_agreement_enabled: false };
      originalSettings = {};
      for (const key of Object.keys(desired)) { assert.ok(Object.hasOwn(before, key), 'Restorable isolated setting required: ' + key); originalSettings[key] = before[key]; }
      fs.writeFileSync(path.join(out, 'settings-before.private.json'), JSON.stringify(originalSettings, null, 2), { flag: 'wx' });
      settingsTouched = true; await json(await admin.api('PUT', '/api/v1/admin/settings', desired));
      registered = await newSession('registered'); const page = registered.page;
      await page.goto(BASE + '/register', { waitUntil: 'domcontentloaded' });
      await page.locator('#login_name').fill(testUser.login); await page.locator('#display_name').fill('测试用户-' + runID);
      await page.locator('#email').fill(''); await page.locator('#password').fill(testUser.password); await page.locator('#confirmPassword').fill(testUser.password);
      const registration = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/auth/register' && r.request().method() === 'POST');
      await page.locator('button[type="submit"]').click();
      const response = await registration, body = await response.json();
      assert.equal(response.status(), 200); assert.equal(body.code, 0); assert.ok(!JSON.stringify(body).includes('sk-'), 'Registration response must not expose API key');
      registered.token = body.data.access_token; secrets.push(registered.token); registered.userID = body.data.user.id; testUser.id = registered.userID;
      result.fixture_ids.registered_user_id = testUser.id;
      assert.ok(Number.isSafeInteger(testUser.id) && testUser.id > 0);
      await page.waitForURL(url => url.pathname !== '/register');
      const snapshot = db('registered', testUser); assert.equal(Number(snapshot.new_wallet_usd), 0); assert.equal(snapshot.default_personal_keys, 1);
      await setupCheck(registered, 'personal');
      await page.getByTestId('setup-scope-team').click(); await page.getByTestId('setup-empty').waitFor({ state: 'visible' });
      assert.equal(await page.getByTestId('setup-copy').isDisabled(), true, 'No team key must not fall back to personal key');
      assert.ok(!/sk-[A-Za-z0-9_-]{16,}/.test(await page.getByTestId('setup-command').inputValue()));
    });

    if (admin?.token) await check('admin-ui-configures-managed-cny-plan-offsale-before-explicit-publish', async () => {
      const paymentConfig = await json(await admin.api('GET', '/api/v1/admin/payment/config'));
      assert.equal(typeof paymentConfig.enabled, 'boolean'); originalPaymentEnabled = paymentConfig.enabled;
      fs.writeFileSync(path.join(out, 'payment-enabled-before.private.json'), JSON.stringify({ enabled: originalPaymentEnabled }), { flag: 'wx' });
      if (!originalPaymentEnabled) await json(await admin.api('PUT', '/api/v1/admin/settings', { payment_enabled: true }));
      await admin.navigate('/admin/orders/plans', '/api/v1/admin/payment/plans');
      await admin.page.getByTestId('create-managed-plan').click();
      await admin.page.getByTestId('managed-name').fill(testUser.login + '-plan');
      await admin.page.getByTestId('managed-group').selectOption(String(fixture.group_id));
      await admin.page.getByTestId('managed-scope').selectOption('personal');
      await admin.page.getByTestId('managed-price').fill('70'); await admin.page.getByTestId('managed-weekly').fill('21');
      await admin.page.getByTestId('managed-allow-balance').check();
      assert.ok((await admin.page.getByTestId('managed-total').innerText()).includes('84.00'));
      const create = admin.page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/admin/payment/plans' && r.request().method() === 'POST');
      const configure = admin.page.waitForResponse(r => /\/api\/v1\/admin\/payment\/plans\/\d+\/managed-entitlement$/.test(new URL(r.url()).pathname) && r.request().method() === 'PUT');
      await admin.page.locator('button[form="realyu-plan-form"]').click();
      const created = await create, raw = created.request().postDataJSON(), plan = await json(created);
      planID = plan.id; result.fixture_ids.plan_id = planID;
      assert.ok(Number.isSafeInteger(planID) && planID > 0); assert.equal(raw.currency, 'CNY'); assert.equal(raw.price, 70); assert.equal(raw.for_sale, false); assert.equal(raw.validity_days, 28);
      const configured = await configure; const payload = configured.request().postDataJSON(); await json(configured);
      assert.equal(payload.weekly_amount_quota, 1500000); assert.equal(payload.total_amount_quota, 6000000); assert.equal(payload.price_cents, 7000);
      const plans = await json(await admin.api('GET', '/api/v1/admin/payment/plans')); const saved = plans.find(p => p.id === planID);
      assert.equal(saved.for_sale, false); assert.equal(saved.managed_entitlement.wallet_price_quota, 5000000);
      await admin.page.locator('#realyu-plan-form').waitFor({ state: 'hidden' });
      const row = admin.page.locator('table tbody tr').filter({ hasText: testUser.login + '-plan' });
      const toggle = admin.page.waitForResponse(r => new URL(r.url()).pathname === `/api/v1/admin/payment/plans/${planID}` && r.request().method() === 'PUT');
      await row.locator('button.relative.inline-flex.h-5.w-9').click(); const published = await toggle; await json(published);
      assert.deepEqual(published.request().postDataJSON(), { for_sale: true });
      await screenshot(admin, 'admin-managed-plan');
    });

    if (registered?.token && testUser.id && planID) await check('wallet-ui-purchase-idempotency-single-debit-and-managed-entitlement', async () => {
      const seed = db('seed-wallet', testUser); assert.equal(Number(seed.new_wallet_usd), 20);
      const checkout = await json(await registered.api('GET', '/api/v1/payment/checkout-info'));
      const firstPlan = checkout.plans.find(p => p.id === planID);
      fs.writeFileSync(path.join(out, 'checkout-plan-first.private.json'), JSON.stringify(firstPlan ?? null, null, 2), { flag: 'wx' });
      assert.ok(firstPlan && Object.hasOwn(firstPlan, 'for_sale'), 'Real checkout response is missing for_sale; UI correctly keeps purchase disabled');
      await registered.navigate('/purchase?tab=subscription', '/api/v1/payment/checkout-info');
      const heading = registered.page.getByRole('heading', { name: testUser.login + '-plan', exact: true });
      await heading.waitFor({ state: 'visible' });
      await heading.locator('..').locator('..').locator('..').getByRole('button').click();
      await registered.page.getByTestId('managed-plan-confirm').waitFor({ state: 'visible' });
      await registered.page.getByTestId('payment-method-grid').getByTitle('按量余额支付', { exact: true }).click();
      const confirm = registered.page.getByTestId('managed-plan-confirm'); assert.ok((await confirm.innerText()).includes('70.00'));
      const orderRequests = [];
      const listener = request => { if (new URL(request.url()).pathname === '/api/v1/payment/orders' && request.method() === 'POST') orderRequests.push(request.postDataJSON()); };
      registered.page.on('request', listener);
      const orderResponse = registered.page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/payment/orders' && r.request().method() === 'POST');
      const submit = registered.page.locator('button').filter({ hasText: /确认支付.*70\.00/ });
      assert.equal(await submit.count(), 1);
      // Two synchronous DOM clicks exercise the real Vue submitting guard; no fake network response.
      await submit.evaluate(button => { button.click(); button.click(); });
      const first = await orderResponse, body = await json(first); registered.page.off('request', listener);
      assert.equal(orderRequests.length, 1); const intent = orderRequests[0];
      assert.equal(intent.payment_type, 'wallet'); assert.equal(intent.plan_id, planID); assert.equal(intent.team_id, 0); assert.equal(intent.purchase_action, 'purchase');
      assert.ok(/^[A-Za-z0-9_-]{16,128}$/.test(intent.idempotency_key));
      assert.equal(body.status, 'COMPLETED'); assert.equal(body.payment_type, 'wallet'); assert.equal(body.currency, 'CNY'); assert.equal(body.amount, 70); assert.equal(body.pay_amount, 0);
      const replay = await json(await registered.api('POST', '/api/v1/payment/orders', intent)); assert.equal(replay.order_id, body.order_id);
      result.fixture_ids.order_id = body.order_id;
      const after = db('purchase', testUser, planID);
      assert.equal(Number(after.new_wallet_usd), 10); assert.equal(after.new_order_count, 1); assert.equal(after.new_managed_count, 1); assert.equal(after.grants.length, 1);
      assert.equal(after.grants[0][3], 5000000); assert.equal(after.orders[0][2], 'COMPLETED'); assert.equal(Number(after.orders[0][4]), 0);
      assert.equal(after.subscriptions[0][2], 6000000); assert.equal(after.subscriptions[0][3], 0); assert.equal(after.subscriptions[0][4], 1500000); assert.equal(after.subscriptions[0][5], 0);
      assert.deepEqual(ownerFunds(after), ownerFunds(baseline));
      const entitlement = await json(await registered.api('GET', '/api/v1/payment/managed-subscriptions'));
      assert.equal(entitlement.user_id, testUser.id); assert.equal(entitlement.managed, true); assert.equal(entitlement.subscriptions.length, 1); assert.equal(entitlement.subscriptions[0].available_quota, 1500000);
      await registered.navigate('/subscriptions', '/api/v1/payment/managed-subscriptions');
      const card = registered.page.getByTestId('entitlement-' + entitlement.subscriptions[0].id); await card.waitFor({ state: 'visible' });
      assert.ok((await card.innerText()).includes('21.00')); assert.equal(await registered.page.getByTestId('entitlements-error').count(), 0);
      result.synthetic_purchase_snapshot = after;
      await screenshot(registered, 'purchased-personal-entitlement');
    });
  } catch (error) { record('suite-preconditions-or-unhandled', false, error.stack || error.message); }
  finally {
    if (nicknameTouched && owner && baseline) await check('cleanup-restores-original-team-alias', async () => {
      await json(await owner.api('PUT', memberPath, { nickname: baseline.member_state[2] }));
      assert.equal(db().member_state[2], baseline.member_state[2]);
    });
    if (planID && admin) await check('cleanup-test-plan-offsale', async () => { await json(await admin.api('PUT', `/api/v1/admin/payment/plans/${planID}`, { for_sale: false })); });
    if (extraOwnerKeyID && owner) await check('cleanup-extra-synthetic-owner-key-disabled', async () => { await json(await owner.api('PUT', `/api/v1/keys/${extraOwnerKeyID}`, { status: 'inactive' })); });
    if (typeof originalPaymentEnabled === 'boolean' && admin) await check('cleanup-restores-isolated-payment-enabled', async () => {
      await json(await admin.api('PUT', '/api/v1/admin/settings', { payment_enabled: originalPaymentEnabled }));
      assert.equal((await json(await admin.api('GET', '/api/v1/admin/payment/config'))).enabled, originalPaymentEnabled);
    });
    if (settingsTouched && admin && originalSettings) await check('cleanup-restores-isolated-signup-settings', async () => {
      await json(await admin.api('PUT', '/api/v1/admin/settings', originalSettings)); const restored = await json(await admin.api('GET', '/api/v1/admin/settings'));
      for (const [key, value] of Object.entries(originalSettings)) assert.deepEqual(restored[key], value, 'Setting restore: ' + key);
    });
    if (baseline) await check('owner-funds-usage-cap-invariant-after-suite', async () => { assert.deepEqual(ownerFunds(db()), ownerFunds(baseline)); });
    if (browser) await browser.close();
    if (result.page_errors.length) record('no-browser-page-errors', false, JSON.stringify(result.page_errors)); else record('no-browser-page-errors', true);
    result.finished_at = new Date().toISOString(); result.passed = result.checks.filter(x => x.pass).length; result.failed = result.checks.filter(x => !x.pass).length;
    save();
    fs.writeFileSync(path.join(out, 'fixture-identities.private.json'), JSON.stringify({ login: testUser.login, user_id: testUser.id, plan_id: planID, extra_owner_key_id: extraOwnerKeyID }, null, 2), { flag: 'wx' });
    const sanitized = { target: BASE, expected_version: expectedVersion, synthetic_only: true, production_modified: false,
      real_browser: Boolean(result.browser_version), paid_model_called: false, clipboard: result.clipboard, started_at: result.started_at, finished_at: result.finished_at,
      candidate_binary_sha256: result.candidate_binary_sha256,
      passed: result.passed, failed: result.failed, page_error_count: result.page_errors.length,
      blocked_request_count: result.blocked_requests.length, checks: result.checks.map(({ name, pass }) => ({ name, pass })),
      evidence_private: out, limitations: ['OS clipboard not used', 'No external payment provider acceptance', 'No model or paid upstream call'] };
    fs.writeFileSync(reportPath, JSON.stringify(sanitized, null, 2), { flag: 'wx' });
    console.log(JSON.stringify({ sanitized_result: reportPath, private_evidence: out, passed: result.passed, failed: result.failed, production_modified: false }));
    if (result.failed) process.exitCode = 1;
  }
}
main().catch(error => { console.error(JSON.stringify({ status: 'HARNESS_FAILED', error: String(error.message).replace(/sk-[A-Za-z0-9_-]+/g, '[REDACTED]') })); process.exitCode = 1; });
