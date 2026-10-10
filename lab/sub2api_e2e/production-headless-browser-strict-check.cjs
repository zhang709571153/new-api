// Prepared for the next reviewed native update. Default is strictly offline.
// Own temporary Edge profile only; no login screenshot, storageState, HAR or trace.
const fs = require('node:fs');
const path = require('node:path');
// Loaded only after the explicit activation flag; offline inspection is portable.

const BASE = 'https://api.realyu.fun';
const ROOT = process.env.REALYU_TEST_RUNTIME || path.resolve(process.cwd(), '.private-realyu-browser');
const RESULTS = process.env.REALYU_TEST_RESULTS || path.join(ROOT, 'results');
const EDGE = process.env.REALYU_TEST_BROWSER || path.join(process.env['ProgramFiles(x86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe');
const args = process.argv.slice(2);
const option = name => { const index = args.indexOf(name); return index < 0 ? undefined : args[index + 1]; };
const secrets = [];
const READ_POSTS = new Set([
  '/api/v1/admin/accounts/usage/batch',
  '/api/v1/admin/accounts/today-stats/batch',
  '/api/v1/admin/dashboard/users-usage',
  '/api/v1/admin/dashboard/api-keys-usage',
  '/api/v1/admin/user-attributes/batch',
  '/api/v1/usage/dashboard/api-keys-usage',
  '/api/v1/admin/payment/managed-subscriptions/query',
]);
function scrub(value) {
  let text = String(value);
  for (const secret of secrets) if (secret) text = text.split(secret).join('[REDACTED]');
  return text.replace(/Bearer\s+[^\s"']+/gi, 'Bearer [REDACTED]')
    .replace(/sk-[A-Za-z0-9_-]+/g, '[REDACTED]').slice(0, 1200);
}
function safePath(url) { return new URL(url).pathname.replace(/\/\d+(?=\/|$)/g, '/:id'); }
function must(condition, code) { if (!condition) throw new Error(code); }
async function readEnvelope(response, code) {
  must(response.status() === 200, code + '_HTTP_' + response.status());
  const payload = await response.json();
  must(payload.code === 0 && payload.data !== undefined, code + '_ENVELOPE_INVALID');
  return payload.data;
}

async function main() {
  // No credential read, HTTP or browser launch before the explicit activation flag.
  if (!args.includes('--active-confirmed')) {
    console.log(JSON.stringify({ status: 'PREPARED_NOT_RUN', network_accessed: false, credentials_read: false,
      requires: '--active-confirmed --expected-version <new deployed version> --credentials <private file>',
      optional: '--expect-currency CNY --expected-rate 7' }));
    return;
  }
  const expectedVersion = option('--expected-version');
  const credentialsPath = option('--credentials');
  const expectedCurrency = option('--expect-currency');
  const expectedRate = option('--expected-rate');
  must(expectedVersion && expectedVersion.length > 8, 'EXACT_NEW_VERSION_REQUIRED');
  must(credentialsPath && fs.existsSync(credentialsPath), 'PRIVATE_CREDENTIALS_PATH_REQUIRED');
  must(fs.existsSync(EDGE), 'EXISTING_EDGE_REQUIRED_NO_DOWNLOAD');
  if (expectedCurrency) must(expectedCurrency === 'CNY' && Number(expectedRate) > 0, 'EXPLICIT_CNY_RATE_REQUIRED');
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const privateDir = path.join(ROOT, 'screenshots', 'production-strict-private-' + stamp);
  fs.mkdirSync(privateDir, { recursive: true });
  fs.mkdirSync(RESULTS, { recursive: true });
  const resultFile = path.join(ROOT, 'production-browser-strict-' + stamp + '.json');
  const sanitizedFile = path.join(RESULTS, 'public-browser-strict-' + stamp + '.json');
  const result = { target: BASE, expected_version: expectedVersion, started_at: new Date().toISOString(),
    kind: 'real-public-independent-headless-browser', existing_user_browser_accessed: false,
    paid_model_called: false, admin_acknowledgement_submitted: false,
    customer_mutations_sent: false, screenshots_private_only: true,
    preflight_requests: [],
    checks: [], network_failures: [], page_errors: [], blocked_requests: [],
    console_errors: [], console_warnings: [], routes: [], private_screenshots: [],
    boundaries: ['No production customer mutation or payment test',
      'Ordinary-user denial is covered by isolated fixture and actual route/JWT regression, not another production user login'],
  };
  const record = (name, pass, detail) => result.checks.push({ name, pass, ...(detail ? { detail } : {}) });
  const publicRead = async endpoint => {
    const response = await fetch(BASE + endpoint, { signal: AbortSignal.timeout(15000), cache: 'no-store',
      headers: { 'User-Agent': 'RealYu-Maintenance/1.0', Accept: 'application/json' } });
    result.preflight_requests.push({ method: 'GET', path: endpoint, status: response.status,
      cf_ray: response.headers.get('cf-ray'), request_id: response.headers.get('x-request-id') });
    return response;
  };
  let browser, context, page, authenticated = false;
  const privateConsole = [];
  const screenshot = async name => {
    if (!authenticated || !page || /\/(login|register|forgot-password|reset-password)(?:\/|$)/.test(new URL(page.url()).pathname)) return;
    if (await page.locator('#email').count() && await page.locator('#password').count()) return;
    const file = path.join(privateDir, name + '.png');
    await page.screenshot({ path: file, fullPage: true, animations: 'disabled' });
    result.private_screenshots.push(file);
  };
  const noGate = async name => {
    must(await page.locator('#admin-compliance-phrase').count() === 0, 'ADMIN_CONFIRMATION_GATE_PRESENT_' + name);
    must(!result.network_failures.some(row => row.status === 423), 'ADMIN_API_423_GATE_PRESENT');
  };
  const gotoAndRead = async (route, endpoint) => {
    const wait = page.waitForResponse(response => new URL(response.url()).pathname === endpoint && response.request().method() === 'GET', { timeout: 25000 });
    const [, response] = await Promise.all([page.goto(BASE + route, { waitUntil: 'domcontentloaded' }), wait]);
    const data = await readEnvelope(response, 'PAGE_' + route.replace(/\W/g, '_'));
    await page.locator('main').first().waitFor({ state: 'visible' });
    must(new URL(page.url()).pathname === route.split('?')[0], 'UNEXPECTED_REDIRECT_' + route);
    await noGate(route);
    result.routes.push({ route, api_status: response.status(), rendered: true });
    return data;
  };
  try {
    // Check exact deployed native identity before opening the private credential file.
    const health = await publicRead('/api/status');
    must(health.status === 200, 'NATIVE_STATUS_HTTP_' + health.status);
    const status = await health.json();
    must(status.success === true && status.data?.single_core === true && status.data?.engine === 'sub2api', 'NATIVE_SINGLECORE_PRECONDITION_FAILED');
    must(status.data.version === expectedVersion, 'DEPLOYED_VERSION_MISMATCH_CREDENTIALS_NOT_READ');
    result.actual_version = status.data.version;
    record('exact-new-native-build-before-credential-entry', true);
    const settingsResponse = await publicRead('/api/v1/settings/public');
    must(settingsResponse.status === 200, 'PUBLIC_SETTINGS_UNAVAILABLE');
    const settingsEnvelope = await settingsResponse.json();
    const settings = settingsEnvelope.data;
    const brandLogo = new URL(settings?.site_logo || '', BASE);
    must(settingsEnvelope.code === 0 && settings?.site_name === 'RealYu API'
      && brandLogo.origin === BASE && brandLogo.pathname === '/brand/realyu-wordmark.png'
      && !brandLogo.hash && [...brandLogo.searchParams.keys()].every(key => key === 'v'), 'REALYU_BRANDING_NOT_APPLIED');
    record('public-brand-settings-match-explicit-seed', true);
    if (expectedCurrency) {
      must(settings.customer_currency === expectedCurrency && Number(settings.customer_usd_to_cny) === Number(expectedRate), 'CUSTOMER_CURRENCY_CONFIG_MISMATCH');
      record('explicit-cny-display-rate-public-contract', true);
    }
    for (const endpoint of ['/api/v1/admin/settings', '/api/v1/admin/users', '/api/v1/pages']) {
      const denied = await publicRead(endpoint);
      must(denied.status === 401, 'UNAUTHENTICATED_ADMIN_NOT_DENIED');
    }
    record('anonymous-admin-and-page-api-denied-401', true);

    const credentials = JSON.parse(fs.readFileSync(credentialsPath, 'utf8').replace(/^\uFEFF/, ''));
    const username = credentials.admin_username, password = credentials.admin_password;
    must(typeof username === 'string' && username && typeof password === 'string' && password, 'PRIVATE_CREDENTIAL_FIELDS_UNAVAILABLE');
    secrets.push(username, password);
    const { chromium } = require(process.env.REALYU_TEST_PLAYWRIGHT_MODULE || 'playwright');
    browser = await chromium.launch({ executablePath: EDGE, headless: true });
    result.browser_version = browser.version();
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', serviceWorkers: 'block' });
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('console', message => {
      if (!['error', 'warning'].includes(message.type())) return;
      const text = scrub(message.text()), location = message.location();
      let host = ''; try { host = new URL(location.url).hostname; } catch {}
      const detail = { type: message.type(), classification: /ERR_BLOCKED_BY_CLIENT|blockedbyclient/i.test(text) ? 'blocked-request' : 'other', source_host: host, line: location.lineNumber };
      (message.type() === 'error' ? result.console_errors : result.console_warnings).push(detail);
      privateConsole.push({ ...detail, message: text });
    });
    page.on('pageerror', error => result.page_errors.push({ name: error.name, message: scrub(error.message) }));
    page.on('response', response => {
      if (new URL(response.url()).origin === BASE && response.status() >= 400) result.network_failures.push({ path: safePath(response.url()), status: response.status(),
        cf_ray: response.headers()['cf-ray'] || null, request_id: response.headers()['x-request-id'] || null });
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), method = request.method();
      if (url.origin !== BASE) {
        result.blocked_requests.push({ host: url.hostname, reason: 'outside_target_origin' });
        return route.abort('blockedbyclient');
      }
      const read = ['GET', 'HEAD', 'OPTIONS'].includes(method);
      const auth = method === 'POST' && ['/api/v1/auth/login', '/api/v1/auth/refresh'].includes(url.pathname);
      const readBatch = method === 'POST' && READ_POSTS.has(url.pathname);
      if (!read && !auth && !readBatch) {
        result.blocked_requests.push({ path: safePath(url.href), method, reason: 'business_write_blocked' });
        return route.abort('blockedbyclient');
      }
      // Batch usage reads may use cache; this acceptance never requests a force refresh.
      if (readBatch) {
        let body; try { body = request.postDataJSON(); } catch {}
        if (body?.force === true) {
          result.blocked_requests.push({ path: safePath(url.href), method, reason: 'force_refresh_blocked' });
          return route.abort('blockedbyclient');
        }
      }
      return route.continue();
    });
    await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
    await page.locator('#email').fill(username);
    await page.locator('#password').fill(password);
    const submit = page.locator('button[type="submit"]');
    must(await submit.isEnabled(), 'UNMET_NATIVE_LOGIN_PREREQUISITE_NO_AUTO_ACCEPT');
    const loginWait = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/auth/login' && response.request().method() === 'POST');
    await submit.click();
    const loginResponse = await loginWait;
    await readEnvelope(loginResponse, 'EXISTING_CREDENTIAL_LOGIN');
    await page.waitForURL(url => url.pathname !== '/login', { timeout: 25000 });
    authenticated = true;
    await page.locator('main').first().waitFor({ state: 'visible' });
    await noGate('after-login');
    record('old-username-password-login-directly-to-native-console', true);

    for (const href of ['/teams', '/keys', '/usage', '/admin/dashboard', '/admin/accounts', '/admin/usage', '/admin/users']) {
      await page.locator('a[href="' + href + '"]').first().waitFor({ state: 'visible' });
    }
    const logo = page.locator('img[src^="/brand/realyu-wordmark.png"]').first();
    await logo.waitFor({ state: 'visible' });
    must(await logo.evaluate(image => image.complete && image.naturalWidth > 0), 'REALYU_LOGO_FAILED_TO_LOAD');
    record('real-native-admin-and-team-menu-with-loaded-realyu-logo', true);

    const workspaceLabel = await page.locator('nav a[href="/admin/dashboard"]').first().innerText();
    must(/工作台|Workbench/i.test(workspaceLabel), 'WORKBENCH_NAV_LABEL_NOT_APPLIED');
    for (const href of ['/redeem', '/admin/redeem', '/admin/promo-codes']) {
      must(await page.locator('nav a[href="' + href + '"]').count() === 0, 'PROMOTION_NAV_ENTRY_STILL_PRESENT');
    }
    const chromeText = (await page.locator('aside, header').allTextContents()).join('\n');
    must(!chromeText.includes(expectedVersion) && !/检查更新|Check for updates|版本更新|Update available/i.test(chromeText), 'VERSION_UPDATE_CHROME_STILL_PRESENT');
    record('workbench-label-and-hidden-version-promotion-navigation', true);

    for (const [route, endpoint, shape] of [
      ['/admin/dashboard', '/api/v1/admin/dashboard/snapshot-v2', 'object'],
      ['/admin/accounts', '/api/v1/admin/accounts', 'items'],
      ['/admin/usage', '/api/v1/admin/usage', 'items'],
      ['/admin/users', '/api/v1/admin/users', 'items'],
    ]) {
      const data = await gotoAndRead(route, endpoint);
      must(data && typeof data === 'object' && (shape !== 'items' || Array.isArray(data.items)), 'ADMIN_PAGE_DATA_SHAPE_INVALID');
      // An authenticated API plus non-empty native main confirms more than a route status.
      must((await page.locator('main').first().innerText()).trim().length > 10, 'ADMIN_PAGE_RENDER_EMPTY');
      record(route + '-native-data-and-page-render', true);
      if (route === '/admin/dashboard') {
        await page.getByTestId('workspace-setup').waitFor({ state: 'visible' });
        await page.getByTestId('setup-scope-personal').waitFor({ state: 'visible' });
        await page.getByTestId('setup-scope-team').waitFor({ state: 'visible' });
        must(await page.getByTestId('setup-list-error').count() === 0, 'WORKBENCH_KEY_LIST_FAILED');
        for (const [client, platform, filename] of [
          ['codex', 'windows', 'setup.ps1'], ['codex', 'unix', 'setup.sh'],
          ['workbuddy', 'windows', 'setup-workbuddy.ps1'], ['workbuddy', 'unix', 'setup-workbuddy.sh'],
        ]) {
          await page.getByTestId('setup-client').selectOption(client);
          await page.getByTestId('setup-platform').selectOption(platform);
          const command = await page.getByTestId('setup-command').inputValue();
          must(command.includes(BASE + '/downloads/realyu/' + filename) && command.includes('sk-****')
            && !/sk-[A-Za-z0-9_-]{16,}/.test(command), 'WORKBENCH_PREVIEW_OR_MASK_INVALID');
        }
        record('workspace-codex-workbuddy-four-masked-command-previews', true);
      }
      await screenshot(route.slice(1).replaceAll('/', '-') + '-private');
    }

    const overviewWait = page.waitForResponse(response => /^\/api\/v1\/realyu\/teams\/\d+$/.test(new URL(response.url()).pathname), { timeout: 25000 });
    const usageWait = page.waitForResponse(response => /^\/api\/v1\/realyu\/teams\/\d+\/usage$/.test(new URL(response.url()).pathname), { timeout: 25000 });
    const [teamList, overviewResponse, usageResponse] = await Promise.all([
      gotoAndRead('/teams', '/api/v1/realyu/teams'), overviewWait, usageWait,
    ]);
    must(Array.isArray(teamList) && teamList.length > 0, 'NO_REAL_TEAM_AVAILABLE_FOR_REQUIRED_RENDER_CHECK');
    const overview = await readEnvelope(overviewResponse, 'REAL_TEAM_OVERVIEW');
    const usage = await readEnvelope(usageResponse, 'REAL_TEAM_USAGE');
    must(Array.isArray(overview.members) && overview.members.length > 0, 'REAL_TEAM_MEMBERS_EMPTY');
    await page.getByTestId('team-select').waitFor({ state: 'visible' });
    await page.getByTestId('usage-stats').waitFor({ state: 'visible' });
    for (const member of overview.members) {
      const row = page.getByTestId('member-' + member.user_id);
      await row.waitFor({ state: 'visible' });
      must((await row.innerText()).includes(member.nickname), 'REAL_TEAM_NICKNAME_NOT_RENDERED');
    }
    const stats = await page.getByTestId('usage-stats').locator('dd').allTextContents();
    must(stats.length === 4 && Number(stats[0]) === usage.stats.requests && Number(stats[1]) === usage.stats.input_tokens && Number(stats[2]) === usage.stats.output_tokens, 'TEAM_USAGE_COUNTER_RENDER_MISMATCH');
    if (expectedCurrency) {
      const expectedCost = '¥' + (Number(usage.stats.cost_usd) * Number(expectedRate)).toFixed(2);
      must(stats[3].trim() === expectedCost, 'TEAM_USAGE_CNY_CONVERSION_MISMATCH');
      record('real-team-usage-cny-matches-native-usd-times-rate', true);
    }
    if (usage.total > 0) {
      await page.getByTestId('usage-items').waitFor({ state: 'visible' });
      must(await page.getByTestId('usage-items').locator('tbody tr').count() === usage.items.length, 'TEAM_USAGE_ROW_COUNT_MISMATCH');
    } else {
      await page.getByTestId('empty-usage').waitFor({ state: 'visible' });
      result.boundaries.push('Selected real team has a valid empty usage interval; no fake request was generated');
    }
    result.team_observation = { available_team_count: teamList.length, rendered_member_count: overview.members.length,
      usage_row_count: usage.items.length, interval_has_usage: usage.total > 0 };
    record('real-team-members-nicknames-and-native-usage-rendered', true);
    await screenshot('real-team-members-and-usage-private');

    if (expectedCurrency) {
      if (settings.model_plaza_enabled) {
        const models = await gotoAndRead('/model-plaza?embedded=1', '/api/v1/model-plaza');
        const count = (models.groups || []).reduce((sum, group) => sum + (group.models || []).length, 0);
        must(count > 0, 'ENABLED_MODEL_PRICE_CATALOG_EMPTY');
        const pricing = page.locator('.plaza-pricing-table');
        await pricing.first().waitFor({ state: 'visible' });
        const text = (await pricing.allTextContents()).join('\n');
        must(/\$|USD|美元/.test(text) && !/¥|￥|CNY|人民币/.test(text), 'MODEL_PRICING_UNIT_NOT_USD');
        record('model-reference-pricing-remains-usd', true);
        await screenshot('model-prices-usd-private');
      } else result.boundaries.push('Native model plaza is disabled; its real-browser pricing table is not claimed as verified');
    }
    await noGate('final');
    record('confirmation-gate-absent-throughout-console-navigation', true);
    record('no-uncaught-javascript-errors', result.page_errors.length === 0);
    record('no-same-origin-http-errors', result.network_failures.length === 0);
    record('no-unexpected-console-errors', result.console_errors.every(row => row.classification === 'blocked-request'));
    record('no-business-write-or-force-refresh-attempts', result.blocked_requests.every(row => row.reason === 'outside_target_origin'));
  } catch (error) {
    record('strict-public-browser-acceptance', false, scrub(error.message));
    try { await screenshot('first-post-login-failure-private'); } catch {}
  } finally {
    if (context) await context.close();
    if (browser) await browser.close();
    result.finished_at = new Date().toISOString();
    result.passed = result.checks.filter(check => check.pass).length;
    result.failed = result.checks.filter(check => !check.pass).length;
    fs.writeFileSync(path.join(privateDir, 'console-private.json'), JSON.stringify(privateConsole, null, 2), { flag: 'wx' });
    fs.writeFileSync(resultFile, JSON.stringify(result, null, 2), { flag: 'wx' });
    // Public summary has no username, team name/id, account list, financial amounts or error text.
    const safe = { ...result, private_screenshots: undefined,
      private_screenshot_count: result.private_screenshots.length,
      checks: result.checks.map(({ name, pass }) => ({ name, pass })),
      page_errors: result.page_errors.map(({ name }) => ({ name })),
    };
    fs.writeFileSync(sanitizedFile, JSON.stringify(safe, null, 2), { flag: 'wx' });
  }
  console.log(JSON.stringify({ result: resultFile, sanitized_result: sanitizedFile, passed: result.passed, failed: result.failed,
    private_screenshot_count: result.private_screenshots.length, paid_model_called: false, customer_mutations_sent: false }));
  process.exitCode = result.failed ? 1 : 0;
}
main().catch(error => { console.error(scrub(error.message)); process.exitCode = 1; });
