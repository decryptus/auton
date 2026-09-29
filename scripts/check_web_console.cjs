/* Real Chromium acceptance, including harmless command execution and screenshots. */
'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createInterface} = require('node:readline');
const fs = require('node:fs/promises');
const {chromium} = require('playwright');
(async () => {
  const fixture = spawn(process.env.PYTHON || 'python', ['scripts/web_console_fixture.py'], {stdio: ['pipe', 'pipe', 'inherit']});
  let browser;
  try {
    const lines = createInterface({input: fixture.stdout});
    const ready = once(lines, 'line');
    const earlyExit = once(fixture, 'exit').then(([code]) => { throw new Error('fixture exited before ready: ' + code); });
    const [line] = await Promise.race([ready, earlyExit]);
    const {uri} = JSON.parse(line);
    browser = await chromium.launch({headless: true, executablePath: process.env.AUTON_BROWSER_EXECUTABLE || undefined});
    const context = await browser.newContext({viewport: {width: 1440, height: 1100}});
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await fs.mkdir('docs/screenshots', {recursive: true});
    await page.goto(uri + '/ui/');
    await page.screenshot({path: 'docs/screenshots/web-login.png', fullPage: true});
    async function login(principal) {
      await page.locator('#username').fill(principal);
      await page.locator('#password').fill('fixture-password-only');
      await page.locator('#login-button').click();
      await page.locator('#console').waitFor({state: 'visible'});
      await page.waitForFunction(() => document.querySelector('#health-value').textContent === 'Ready');
      await page.locator('#auto-refresh').uncheck();
    }
    await login('operator');
    assert.equal(await page.locator('#principal').textContent(), 'operator');
    assert.equal(await page.evaluate(() => document.cookie), '');
    const cookies = await context.cookies();
    assert(cookies.some(cookie => cookie.name === 'autond-session' && cookie.httpOnly && cookie.sameSite === 'Strict'));
    await page.getByRole('button', {name: 'check-storage', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#stdout').textContent.includes('Local storage ready'));
    await page.screenshot({path: 'docs/screenshots/web-console.png', fullPage: true});
    await page.getByRole('button', {name: 'check-output', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#stdout').textContent.includes('<img'));
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    assert.equal(await page.locator('#stdout img').count(), 0);
    await page.locator('#search').fill('does-not-exist');
    assert.equal(await page.locator('#jobs tr').count(), 0);
    await page.locator('#search').fill('');
    await page.locator('#auto-refresh').check();
    await page.locator('#new-job').click();
    await page.locator('#run-endpoint').selectOption('diagnostic');
    await page.locator('#run-args').fill('-c\nprint("browser execution verified")');
    assert.equal(await page.locator('#run-confirm').isChecked(), false);
    await page.locator('#run-confirm').check();
    let posts = 0;
    page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/run/')) posts++; });
    await page.locator('#submit-run').click();
    await page.waitForFunction(() => !document.querySelector('#run-dialog').open);
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#stdout').textContent.includes('browser execution verified'));
    assert.equal(posts, 1);
    await page.locator('#auto-refresh').uncheck();
    // A mutation overlapping an older refresh must schedule a fresh read even
    // with automatic refresh off; otherwise stale "Ready" can hide maintenance.
    let releaseHealth, capturedHealth;
    const heldHealth = new Promise(resolve => { releaseHealth = resolve; });
    const healthCaptured = new Promise(resolve => { capturedHealth = resolve; });
    await page.route('**/health', async route => {
      const response = await route.fetch(); capturedHealth();
      await heldHealth; await route.fulfill({response});
    });
    await page.locator('#refresh').click();
    await healthCaptured;
    page.once('dialog', dialog => dialog.accept());
    const maintenanceChanged = page.waitForResponse(response => response.url().endsWith('/maintenance') && response.status() === 200);
    await page.locator('#maintenance').click();
    await maintenanceChanged; releaseHealth();
    await page.waitForFunction(() => document.querySelector('#health-value').textContent === 'Maintenance');
    await page.unroute('**/health');
    assert.equal(await page.locator('#new-job').isDisabled(), true);
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: 'docs/screenshots/web-mobile.png', fullPage: true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#maintenance').click();
    await page.waitForFunction(() => document.querySelector('#health-value').textContent === 'Ready');
    await page.reload();
    await page.locator('#console').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.querySelector('#health-value').textContent === 'Ready');
    await page.locator('#auto-refresh').uncheck();
    // Lose a successful POST response. The browser must expose uncertainty and
    // must never replay it, including after a read-only refresh.
    await page.route('**/run/**', async route => { await route.fetch(); await route.abort('failed'); });
    await page.locator('#new-job').click();
    await page.locator('#run-args').fill('-c\nprint("ambiguous response test")');
    await page.locator('#run-confirm').check();
    await page.locator('#submit-run').click();
    await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('Submission outcome unknown'));
    assert.equal(posts, 2);
    await page.locator('#refresh').click();
    assert.equal(posts, 2);
    await page.unroute('**/run/**');
    await page.locator('#logout').click();
    await page.locator('#login-panel').waitFor({state: 'visible'});
    assert.equal((await context.cookies()).filter(cookie => cookie.name === 'autond-session').length, 0);
    await login('reader');
    assert.equal(await page.locator('#new-job').isDisabled(), true);
    assert.equal(await page.locator('#maintenance-controls').isVisible(), false);
    assert.equal(await page.locator('#jobs tr').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Browser acceptance passed: login, output safety, confirmation, maintenance, mobile, reload, no replay, logout and read-only access.');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
