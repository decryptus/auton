/* Real cross-site OIDC navigation with mTLS, scoped local account and logout. */
'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createInterface} = require('node:readline');
const fs = require('node:fs/promises');
const {chromium} = require('playwright');
(async () => {
  const fixture = spawn(process.env.PYTHON || 'python', ['scripts/oidc_browser_fixture.py'], {stdio: ['pipe', 'pipe', 'inherit']});
  let browser;
  try {
    const lines = createInterface({input: fixture.stdout});
    const [line] = await Promise.race([once(lines, 'line'), once(fixture, 'exit').then(([code]) => { throw new Error('fixture failed: ' + code); })]);
    const {uri, cert, key} = JSON.parse(line);
    browser = await chromium.launch({headless: true, executablePath: process.env.AUTON_BROWSER_EXECUTABLE || undefined});
    // Ephemeral CA trust is covered by the strict requests mTLS suite.
    const context = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1280, height: 900},
      clientCertificates: [{origin: uri, certPath: cert, keyPath: key}]});
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(uri + '/ui/');
    await page.locator('#sso-login').waitFor({state: 'visible'});
    await page.locator('#sso-login').click();
    await page.locator('#console').waitFor({state: 'visible'});
    assert.equal(await page.locator('#principal').textContent(), 'reader');
    assert.equal(await page.locator('#new-job').isDisabled(), true);
    assert.equal(new URL(page.url()).search, '');
    assert.equal(await page.evaluate(() => document.cookie), '');
    const cookies = await context.cookies();
    assert(cookies.some(c => c.name === '__Host-autond-session' && c.httpOnly && c.secure && c.sameSite === 'Strict'));
    await fs.mkdir('docs/screenshots', {recursive: true});
    await page.screenshot({path: 'docs/screenshots/web-sso.png', fullPage: true});
    await page.locator('#logout').click();
    await page.locator('#login-panel').waitFor({state: 'visible'});
    assert.deepEqual(errors, []);
    await context.close();
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
