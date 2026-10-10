// One explicitly authorized public synthetic signup. Default is strictly offline.
// No purchase, funds seed, model call, customer change, SQL write or row deletion.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const BASE = 'https://api.realyu.fun';
const args = process.argv.slice(2);
const option = name => { const i = args.indexOf(name); return i < 0 ? undefined : args[i + 1]; };
const must = (condition, code) => { if (!condition) throw new Error(code); };
const readJSON = file => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
const secrets = [];
const scrub = value => {
  let text = String(value);
  for (const value of secrets) if (value) text = text.split(value).join('[REDACTED]');
  return text.replace(/sk-[A-Za-z0-9_-]+/g, '[REDACTED]').replace(/Bearer\s+[^\s"']+/gi, 'Bearer [REDACTED]').slice(0, 700);
};
const safePath = url => new URL(url).pathname.replace(/\/\d+(?=\/|$)/g, '/:id');

async function main() {
  if (!args.includes('--active-confirmed') || !args.includes('--settings-live')) {
    console.log(JSON.stringify({ status: 'PREPARED_NOT_RUN', network_accessed: false, credentials_read: false,
      requires: '--active-confirmed --settings-live --expected-version VERSION --expected-sha SHA --credentials PRIVATE_FILE --manifest PRIVATE_MANIFEST --expected-pg-port PORT --expected-database DB --python EXISTING_PYTHON',
      max_created_users: 1, purchase_submitted: false, paid_model_called: false }));
    return;
  }
  const expectedVersion = option('--expected-version'), expectedSHA = option('--expected-sha');
  const credentialsPath = option('--credentials'), manifest = option('--manifest');
  const pgPort = option('--expected-pg-port'), database = option('--expected-database'), python = option('--python');
  const runtime = process.env.REALYU_TEST_RUNTIME || path.resolve(process.cwd(), '.private-realyu-browser');
  const results = process.env.REALYU_TEST_RESULTS || path.join(runtime, 'results');
  const edge = process.env.REALYU_TEST_BROWSER || path.join(process.env['ProgramFiles(x86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe');
  must(expectedVersion && /^[a-f0-9]{64}$/.test(expectedSHA || '') && credentialsPath && manifest && pgPort && database && python, 'EXPLICIT_TARGET_ARGUMENTS_REQUIRED');
  must(fs.existsSync(edge) && fs.existsSync(python), 'EXISTING_LOCAL_RUNTIME_REQUIRED_NO_DOWNLOAD');
  const started = new Date(), stamp = started.toISOString().replace(/[:.]/g, '-');
  const out = path.join(runtime, 'screenshots', 'production-signup-private-' + stamp);
  fs.mkdirSync(out, { recursive: true }); fs.mkdirSync(results, { recursive: true });
  const nonce = crypto.randomBytes(8).toString('hex');
  const identity = { login: 'ux-public-e2e-' + started.toISOString().slice(0, 10).replaceAll('-', '') + '-' + nonce,
    nickname: '发布验收-' + nonce.slice(0, 6), started_at: started.toISOString(), user_id: null, key_id: null };
  const password = 'E2e!' + crypto.randomBytes(24).toString('base64url') + 'a9';
  secrets.push(identity.login, identity.nickname, password);
  const identityPath = path.join(out, 'identity-current.private.json');
  fs.writeFileSync(path.join(out, 'identity-original.private.json'), JSON.stringify(identity, null, 2), { flag: 'wx' });
  const saveIdentity = () => fs.writeFileSync(identityPath, JSON.stringify(identity, null, 2));
  saveIdentity();
  const report = { target: BASE, started_at: started.toISOString(), expected_version: expectedVersion,
    kind: 'one-public-synthetic-signup-browser', registration_attempts: 0, created_users: 0,
    existing_customer_mutated: false, database_readonly: true, rows_deleted: false,
    funds_seeded: false, purchase_submitted: false, paid_model_called: false, retries: 0,
    existing_browser_accessed: false, admin_acknowledgement_submitted: false,
    checks: [], requests: [], blocked_requests: [], network_errors: [], page_errors: [],
    console_errors: [], cleanup: { attempted: false, user_disabled: false, key_inactive: false },
    boundaries: ['No merchant or wallet transaction submitted', 'Only the one newly generated synthetic identity may be changed'],
  };
  let browser, context, page, adminToken, userToken, authenticated = false;
  let cleaning = false, snapshotNumber = 0;
  const record = (name, pass, code) => report.checks.push({ name, pass, ...(code ? { code: scrub(code) } : {}) });
  const firstFailure = (name, error) => { record(name, false, error.message); if (!report.first_failure) report.first_failure = { name, code: scrub(error.message) }; };
  const jsonEnvelope = async (response, code) => {
    const status = typeof response.status === 'function' ? response.status() : response.status;
    must(status === 200, code + '_HTTP_' + status);
    const data = await response.json();
    must(data.code === 0 && data.data !== undefined, code + '_INVALID_ENVELOPE');
    return data.data;
  };
  const api = async (method, endpoint, token, body) => {
    const login = method === 'POST' && endpoint === '/api/v1/auth/login';
    const ownKey = cleaning && method === 'PUT' && endpoint === '/api/v1/keys/' + identity.key_id && token === userToken
      && body?.status === 'inactive' && Object.keys(body).length === 1;
    const ownUser = cleaning && method === 'PUT' && endpoint === '/api/v1/admin/users/' + identity.user_id && token === adminToken
      && body?.status === 'disabled' && Object.keys(body).length === 1;
    must(method === 'GET' || login || ownKey || ownUser, 'API_WRITE_OUTSIDE_EXACT_TEST_CLEANUP');
    const response = await fetch(BASE + endpoint, { method, signal: AbortSignal.timeout(25000), cache: 'no-store',
      headers: { 'User-Agent': 'RealYu-Maintenance/1.0', Accept: 'application/json',
        ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined });
    report.requests.push({ method, path: safePath(BASE + endpoint), status: response.status,
      cf_ray: response.headers.get('cf-ray'), request_id: response.headers.get('x-request-id') });
    return response;
  };
  const db = mode => {
    saveIdentity();
    let raw;
    try {
      raw = execFileSync(python, [path.join(__dirname, 'production-signup-readonly-db.py'), '--active-confirmed',
        '--manifest', manifest, '--identity-file', identityPath, '--expected-sha', expectedSHA,
        '--expected-pg-port', pgPort, '--expected-database', database, '--mode', mode], { encoding: 'utf8', timeout: 15000, windowsHide: true });
    } catch (error) {
      raw = String(error.stdout || '');
      fs.writeFileSync(path.join(out, `pg-${++snapshotNumber}-${mode}-failed.private.json`), raw || JSON.stringify({ error: 'READONLY_HELPER_FAILED' }), { flag: 'wx' });
      let detail; try { detail = JSON.parse(raw); } catch {}
      throw new Error('READONLY_PG_' + (detail?.error_code || 'FAILED'));
    }
    const value = JSON.parse(raw);
    fs.writeFileSync(path.join(out, `pg-${++snapshotNumber}-${mode}.private.json`), JSON.stringify(value, null, 2), { flag: 'wx' });
    must(value.pass, 'READONLY_PG_CHECK_FAILED');
    return value;
  };
  const screenshot = async name => {
    if (!authenticated || !page || /\/(login|register)$/.test(new URL(page.url()).pathname)) return;
    await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true, animations: 'disabled' });
  };
  const pageRead = async (route, endpoint) => {
    const wait = page.waitForResponse(r => new URL(r.url()).pathname === endpoint && r.request().method() === 'GET', { timeout: 25000 });
    const [, response] = await Promise.all([page.goto(BASE + route, { waitUntil: 'domcontentloaded' }), wait]);
    const value = await jsonEnvelope(response, 'PAGE_READ');
    must(new URL(page.url()).pathname === route.split('?')[0], 'PAGE_REDIRECT_UNEXPECTED');
    await page.locator('main').first().waitFor({ state: 'visible' });
    return value;
  };
  try {
    const statusResponse = await api('GET', '/api/status');
    must(statusResponse.status === 200, 'PUBLIC_STATUS_UNAVAILABLE');
    const status = await statusResponse.json();
    must(status.success && status.data?.single_core && status.data?.engine === 'sub2api' && status.data?.version === expectedVersion, 'PUBLIC_NATIVE_IDENTITY_MISMATCH');
    const settings = await jsonEnvelope(await api('GET', '/api/v1/settings/public'), 'PUBLIC_SETTINGS');
    must(settings.registration_enabled && settings.username_registration_enabled && settings.payment_enabled
      && settings.model_plaza_enabled && settings.customer_currency === 'CNY' && Number(settings.customer_usd_to_cny) === 7, 'SETTINGS_LIVE_PRECONDITIONS_NOT_MET');
    must(!settings.email_verify_enabled && !settings.invitation_code_enabled && !settings.promo_code_enabled
      && !settings.turnstile_enabled && !settings.tencent_captcha_enabled && !settings.aliyun_captcha_enabled
      && !settings.login_agreement_enabled, 'MANUAL_SIGNUP_PREREQUISITE_ENABLED');
    record('live-version-brand-registration-payment-model-settings', true);
    db('preflight'); record('new-random-synthetic-identity-absent-before-registration', true);

    const credentials = readJSON(credentialsPath);
    must(credentials.admin_username && credentials.admin_password, 'ADMIN_CLEANUP_CREDENTIALS_REQUIRED');
    secrets.push(credentials.admin_username, credentials.admin_password);
    const adminLogin = await jsonEnvelope(await api('POST', '/api/v1/auth/login', null,
      { email: credentials.admin_username, password: credentials.admin_password }), 'ADMIN_CLEANUP_LOGIN');
    must(adminLogin.user?.role === 'admin' && adminLogin.access_token, 'ADMIN_CLEANUP_AUTHORITY_UNAVAILABLE');
    adminToken = adminLogin.access_token; secrets.push(adminToken);
    record('cleanup-authority-confirmed-before-creating-test-user', true);

    const { chromium } = require(process.env.REALYU_TEST_PLAYWRIGHT_MODULE || 'playwright');
    browser = await chromium.launch({ executablePath: edge, headless: true });
    report.browser_version = browser.version();
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', serviceWorkers: 'block' });
    page = await context.newPage(); page.setDefaultTimeout(20000);
    page.on('pageerror', error => report.page_errors.push({ name: error.name }));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      const text = scrub(message.text());
      report.console_errors.push({ classification: /ERR_BLOCKED_BY_CLIENT|blockedbyclient/i.test(text) ? 'blocked-request' : 'other' });
    });
    page.on('response', response => {
      if (new URL(response.url()).origin === BASE && response.status() >= 400) report.network_errors.push({ path: safePath(response.url()), status: response.status(),
        cf_ray: response.headers()['cf-ray'] || null, request_id: response.headers()['x-request-id'] || null });
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), method = request.method();
      if (url.origin !== BASE) { report.blocked_requests.push({ host: url.hostname, reason: 'outside_target_origin' }); return route.abort('blockedbyclient'); }
      if (['GET', 'HEAD', 'OPTIONS'].includes(method)) return route.continue();
      if (method === 'POST' && url.pathname === '/api/v1/auth/refresh') return route.continue();
      if (method === 'POST' && url.pathname === '/api/v1/usage/dashboard/api-keys-usage') return route.continue();
      if (method === 'POST' && url.pathname === '/api/v1/auth/register' && report.registration_attempts === 0) {
        const body = request.postDataJSON();
        if (body?.login_name === identity.login && body.display_name === identity.nickname && body.password === password && !body.email && !body.promo_code) {
          report.registration_attempts++; return route.continue();
        }
      }
      report.blocked_requests.push({ path: safePath(url.href), method, reason: 'business_write_blocked' });
      return route.abort('blockedbyclient');
    });
    await page.goto(BASE + '/register', { waitUntil: 'domcontentloaded' });
    await page.locator('#login_name').fill(identity.login); await page.locator('#display_name').fill(identity.nickname);
    await page.locator('#email').fill(''); await page.locator('#password').fill(password); await page.locator('#confirmPassword').fill(password);
    must(await page.locator('#promo_code').count() === 0, 'PROMOTION_REGISTRATION_FIELD_VISIBLE');
    const registerWait = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/auth/register' && r.request().method() === 'POST', { timeout: 30000 });
    await page.locator('button[type="submit"]').click();
    const registeredResponse = await registerWait;
    report.requests.push({ method: 'POST', path: '/api/v1/auth/register', status: registeredResponse.status(),
      cf_ray: registeredResponse.headers()['cf-ray'] || null, request_id: registeredResponse.headers()['x-request-id'] || null });
    const registered = await jsonEnvelope(registeredResponse, 'REAL_BROWSER_REGISTER');
    must(!JSON.stringify(registered).includes('sk-'), 'REGISTRATION_RESPONSE_EXPOSES_KEY');
    must(Number.isSafeInteger(registered.user?.id) && registered.user.id > 0 && registered.access_token, 'REGISTER_IDENTITY_MISSING');
    identity.user_id = registered.user.id; userToken = registered.access_token; secrets.push(userToken); saveIdentity();
    report.created_users = 1;
    await page.waitForURL(url => url.pathname === '/dashboard', { timeout: 25000 }); authenticated = true;
    await page.getByTestId('workspace-setup').waitFor({ state: 'visible' });
    must(await page.locator('#admin-compliance-phrase').count() === 0, 'ADMIN_CONFIRMATION_GATE_PRESENT');
    must(registered.user.display_name === identity.nickname && !registered.user.email && Number(registered.user.balance) === 0, 'REGISTER_USERNAME_NICKNAME_OR_ZERO_BALANCE_MISMATCH');
    record('one-no-email-username-nickname-registration-to-workbench', true);

    const state = db('inspect'); must(state.exists && state.user_id === identity.user_id && state.user_status === 'active' && state.key_status === 'active', 'REGISTERED_PG_IDENTITY_MISMATCH');
    identity.key_id = state.key_id; saveIdentity();
    must(state.zero_balance && state.zero_key_usage && Object.values(state.counts).every(value => value === 0), 'NEW_USER_UNEXPECTED_FUNDS_OR_ACTIVITY');
    record('exactly-one-personal-key-no-wallet-grants-subscriptions-orders-or-team', true);
    const keys = await jsonEnvelope(await api('GET', '/api/v1/keys?page=1&page_size=100', userToken), 'NEW_KEYS');
    must(keys.total === 1 && keys.items.length === 1 && keys.items[0].id === identity.key_id
      && keys.items[0].user_id === identity.user_id && keys.items[0].realyu_scope?.kind === 'personal'
      && keys.items[0].realyu_scope.active, 'NEW_PERSONAL_KEY_API_SCOPE_MISMATCH');
    for (const key of keys.items) if (key.key) secrets.push(key.key);
    await page.getByTestId('setup-copy').waitFor({ state: 'visible' });
    must(await page.getByTestId('setup-copy').isEnabled(), 'ZERO_BALANCE_PREVENTS_CLIENT_CONFIGURATION');
    must((await page.getByTestId('setup-command').inputValue()).includes('sk-****'), 'NEW_USER_PREVIEW_UNMASKED');
    await page.getByTestId('setup-scope-team').click();
    await page.getByTestId('setup-empty').waitFor({ state: 'visible' });
    must(await page.getByTestId('setup-copy').isDisabled(), 'TEAM_SCOPE_FELL_BACK_TO_PERSONAL_KEY');
    record('zero-balance-configuration-available-team-scope-empty-without-fallback', true);
    await screenshot('new-user-workbench-private');

    const checkout = await pageRead('/purchase?tab=subscription', '/api/v1/payment/checkout-info');
    must(checkout.balance_disabled === true && checkout.plans?.length === 6, 'EXPECTED_SIX_MANAGED_PLANS_NOT_AVAILABLE');
    for (const plan of checkout.plans) {
      must(plan.for_sale && plan.currency === 'CNY' && plan.managed_entitlement?.funding_scope === 'personal'
        && plan.managed_entitlement.capabilities.purchase && plan.managed_entitlement.capabilities.wallet_payment, 'MANAGED_PERSONAL_PLAN_CONTRACT_MISMATCH');
      await page.getByRole('heading', { name: plan.name, exact: true }).waitFor({ state: 'visible' });
    }
    record('six-published-managed-personal-plans-real-purchase-page', true);
    await screenshot('six-plan-catalog-private');
    const plan = checkout.plans[0];
    const heading = page.getByRole('heading', { name: plan.name, exact: true });
    await heading.locator('xpath=ancestor::div[contains(@class,"group")][1]').getByRole('button').click();
    await page.getByTestId('managed-plan-confirm').waitFor({ state: 'visible' });
    await page.getByTestId('payment-method-grid').getByTitle('按量余额支付', { exact: true }).waitFor({ state: 'visible' });
    must(report.blocked_requests.every(row => row.reason === 'outside_target_origin'), 'UNEXPECTED_BUSINESS_WRITE_ATTEMPT');
    record('wallet-option-visible-without-submitting-purchase', true);
    await screenshot('wallet-option-preview-private');

    const modelData = await pageRead('/model-plaza?embedded=1', '/api/v1/model-plaza');
    const modelCount = (modelData.groups || []).reduce((n, group) => n + (group.models || []).length, 0);
    must(modelCount === 9, 'EXPECTED_NINE_AUTHORIZED_MODELS_NOT_VISIBLE');
    const pricing = page.locator('.plaza-pricing-table');
    await pricing.first().waitFor({ state: 'visible' });
    const priceText = (await pricing.allTextContents()).join('\n');
    must(/\$|USD|美元/.test(priceText) && !/¥|￥|CNY|人民币/.test(priceText), 'MODEL_PRICE_TABLE_NOT_USD');
    record('new-user-nine-authorized-models-reference-pricing-usd', true);
    await screenshot('authorized-models-usd-private');
    const final = db('inspect'); must(final.exists && final.user_id === identity.user_id && final.key_id === identity.key_id, 'PRE_CLEANUP_IDENTITY_MISMATCH');
    must(final.zero_balance && final.zero_key_usage && Object.values(final.counts).every(value => value === 0), 'PREVIEW_CHANGED_FUNDS_OR_ACTIVITY');
    record('read-only-preview-left-zero-funds-orders-grants-and-usage', true);
    record('no-unexpected-browser-errors', report.page_errors.length === 0 && report.network_errors.length === 0
      && report.console_errors.every(row => row.classification === 'blocked-request'));
  } catch (error) {
    firstFailure('public-signup-browser-acceptance', error);
    try { await screenshot('first-post-register-failure-private'); } catch {}
  } finally {
    // Stop browser activity before precise cleanup. Never retry a registration or purchase.
    if (context) await context.close(); if (browser) await browser.close();
    if (report.registration_attempts > 0) {
      report.cleanup.attempted = true;
      try {
        const found = db('inspect');
        if (found.exists) {
          identity.user_id = found.user_id; identity.key_id = found.key_id; saveIdentity(); report.created_users = 1;
          must(adminToken, 'ADMIN_CLEANUP_TOKEN_MISSING');
          const adminUser = await jsonEnvelope(await api('GET', '/api/v1/admin/users/' + identity.user_id, adminToken), 'CLEANUP_IDENTITY');
          must(adminUser.id === identity.user_id && adminUser.login_name === identity.login && adminUser.display_name === identity.nickname
            && adminUser.role === 'user' && new Date(adminUser.created_at).getTime() >= started.getTime() - 5000, 'ADMIN_CLEANUP_CORRELATION_FAILED');
          if (!userToken && found.user_status === 'active') {
            const cleanupLogin = await jsonEnvelope(await api('POST', '/api/v1/auth/login', null, { email: identity.login, password }), 'CLEANUP_ONLY_LOGIN_AFTER_UNKNOWN_REGISTER');
            must(cleanupLogin.user?.id === identity.user_id, 'CLEANUP_LOGIN_ID_MISMATCH'); userToken = cleanupLogin.access_token; secrets.push(userToken);
          }
          cleaning = true;
          if (found.key_status !== 'inactive') {
            const key = await jsonEnvelope(await api('PUT', '/api/v1/keys/' + identity.key_id, userToken, { status: 'inactive' }), 'DISABLE_EXACT_NEW_KEY');
            if (key.key) secrets.push(key.key);
            must(key.id === identity.key_id && key.user_id === identity.user_id && key.status === 'inactive', 'KEY_DISABLE_NOT_CONFIRMED');
          }
          const disabled = await jsonEnvelope(await api('PUT', '/api/v1/admin/users/' + identity.user_id, adminToken, { status: 'disabled' }), 'DISABLE_EXACT_NEW_USER');
          must(disabled.id === identity.user_id && disabled.status === 'disabled', 'USER_DISABLE_NOT_CONFIRMED');
          const after = db('cleaned'); must(after.exists, 'CLEANED_IDENTITY_LOST');
          const keysAfter = await jsonEnvelope(await api('GET', '/api/v1/admin/users/' + identity.user_id + '/api-keys?page=1&page_size=100', adminToken), 'ADMIN_CLEANUP_KEY_VERIFY');
          must(keysAfter.total === 1 && keysAfter.items[0].id === identity.key_id && keysAfter.items[0].status === 'inactive', 'ADMIN_CLEANUP_KEY_NOT_INACTIVE');
          report.cleanup.user_disabled = true; report.cleanup.key_inactive = true;
          record('cleanup-exact-new-key-inactive-user-disabled-no-delete', true);
        } else record('registration-failed-before-identity-created-no-cleanup-target', true);
      } catch (error) { firstFailure('synthetic-cleanup', error); }
    }
    report.finished_at = new Date().toISOString(); report.passed = report.checks.filter(x => x.pass).length; report.failed = report.checks.filter(x => !x.pass).length;
    fs.writeFileSync(path.join(out, 'result.private.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
    const safe = { ...report, checks: report.checks.map(({ name, pass }) => ({ name, pass })),
      first_failure: report.first_failure ? { name: report.first_failure.name, code: /^[A-Z0-9_]+$/.test(report.first_failure.code) ? report.first_failure.code : 'PRIVATE_DIAGNOSTIC_AVAILABLE' } : undefined };
    const safeFile = path.join(results, 'public-signup-browser-' + stamp + '.json');
    fs.writeFileSync(safeFile, JSON.stringify(safe, null, 2), { flag: 'wx' });
    console.log(JSON.stringify({ evidence_private: out, sanitized_result: safeFile, passed: report.passed, failed: report.failed,
      created_users: report.created_users, cleanup: report.cleanup, paid_model_called: false, purchase_submitted: false }));
    process.exitCode = report.failed ? 1 : 0;
  }
}
main().catch(error => { console.error(scrub(error.message)); process.exitCode = 1; });
