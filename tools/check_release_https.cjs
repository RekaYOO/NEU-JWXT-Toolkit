const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const { randomBytes, createHash, X509Certificate } = require('node:crypto');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const http = require('node:http');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return server.address().port;
}

async function main() {
  const executable = await fs.realpath(process.argv[2]);
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'toolbox-https-'));
  let proxy, child, stopped, browser;
  try {
    const key = path.join(temp, 'key.pem'), cert = path.join(temp, 'cert.pem');
    execFileSync(process.env.OPENSSL || 'openssl', ['req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost', '-keyout', key, '-out', cert], { stdio: 'ignore', windowsHide: true });
    const pem = await fs.readFile(cert);
    const pin = createHash('sha256').update(new X509Certificate(pem).publicKey.export({ format: 'der', type: 'spki' })).digest('base64');
    const reservation = http.createServer();
    const port = await listen(reservation);
    await new Promise(resolve => reservation.close(resolve));
    proxy = https.createServer({ key: await fs.readFile(key), cert: pem }, (req, res) => {
      const upstream = http.request({ hostname: '127.0.0.1', port, path: req.url, method: req.method, headers: { ...req.headers, 'x-forwarded-proto': 'https' } }, reply => {
        res.writeHead(reply.statusCode, reply.headers); reply.pipe(res);
      });
      upstream.on('error', () => { res.writeHead(502); res.end(); });
      req.pipe(upstream);
    });
    const origin = `https://127.0.0.1:${await listen(proxy)}`;
    const access = randomBytes(24).toString('hex'), admin = randomBytes(24).toString('hex');
    child = spawn(executable, ['--listen', `127.0.0.1:${port}`, '--mode', 'remote-multi', '--public-origin', origin], { cwd: temp, windowsHide: true, stdio: 'ignore', env: { ...process.env, NEU_JWXT_DATA_DIR: path.join(temp, 'data'), NEU_JWXT_ACCESS_PASSWORD: access, NEU_JWXT_ADMIN_PASSWORD: admin } });
    stopped = once(child, 'exit');
    browser = await chromium.launch({ channel: 'chrome', args: [`--ignore-certificate-errors-spki-list=${pin}`] });
    const page = await browser.newPage();
    await page.goto(`${origin}/api/health`);
    await page.waitForFunction(async () => (await fetch('/api/health')).ok, { }, { timeout: 15000 });
    const health = await page.evaluate(async () => (await fetch('/api/health', { credentials: 'omit', redirect: 'error' })).json());
    assert.equal(health.mobile_api_version, 1, 'Android client requires mobile API version 1 before login');
    const call = (route, body) => page.evaluate(async ([route, body]) => (await fetch(route, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})).status, [route, body]);
    assert.equal(await call('/api/admin/accounts'), 401);
    await page.goto(origin);
    const password = page.getByPlaceholder('输入服务器访问密码');
    const submit = page.getByRole('button', { name: '进入工具箱' });
    await password.fill('incorrect-fixture-password');
    await submit.click();
    await page.getByText('密码验证失败', { exact: true }).waitFor();
    assert.equal(await call('/api/status'), 401);
    await password.fill(access);
    await submit.click();
    await page.getByPlaceholder('学号', { exact: true }).waitFor();
    assert.equal(await call('/api/admin/accounts'), 401);
    await page.goto(`${origin}/admin`);
    await page.getByLabel('管理员密码').fill(admin);
    await page.getByRole('button', { name: '进入管理' }).click();
    const account = '20990001';
    const unaffected = '20990002';
    for (const identity of [account, unaffected]) {
      await page.getByLabel('准入学号').fill(identity);
      await page.getByRole('button', { name: '添加账号' }).click();
      await page.getByRole('listitem').filter({ hasText: identity }).getByText('准入已启用', { exact: true }).waitFor();
    }
    const row = page.getByRole('listitem').filter({ hasText: account });
    const verifyEnabled = async enabled => {
      await row.getByText(enabled ? '准入已启用' : '准入已停用', { exact: true }).waitFor();
      const accounts = await page.evaluate(async () => (await (await fetch('/api/admin/accounts')).json()).accounts);
      assert.equal(accounts.find(item => item.account === account)?.enabled, enabled);
      assert.equal(accounts.find(item => item.account === unaffected)?.enabled, true);
      await page.getByRole('listitem').filter({ hasText: unaffected }).getByText('准入已启用', { exact: true }).waitFor();
    };
    await verifyEnabled(true);
    for (const [action, enabled] of [['停用账号', false], ['恢复准入', true], ['移出准入名单', false], ['恢复准入', true]]) {
      await row.getByRole('button', { name: action }).click();
      await page.getByRole('dialog').getByRole('button', { name: /^确\s*认$/ }).click();
      await verifyEnabled(enabled);
    }
    assert.equal(await call('/api/admin/accounts'), 200);
    for (const name of ['neu_jwxt_access', 'neu_toolbox_admin']) {
      const cookie = (await page.context().cookies(origin)).find(item => item.name === name);
      assert(cookie?.httpOnly && cookie.secure && cookie.sameSite === 'Lax');
    }
    await page.getByRole('button', { name: '退出站点', exact: true }).click();
    await password.waitFor();
    assert.equal(await call('/api/admin/accounts'), 401);
    await page.reload();
    await password.waitFor();
    await password.fill(access);
    await submit.click();
    await page.getByPlaceholder('学号', { exact: true }).waitFor();
    assert.equal(await call('/api/admin/accounts'), 401);
    await page.goto(`${origin}/admin`);
    await page.getByLabel('管理员密码').waitFor();
    console.log('PASS Chrome HTTPS: gateway retry, admin admission/disable/enable, secure cookies, site logout button, no admin access after gateway re-login');
  } finally {
    await browser?.close();
    if (child && child.exitCode === null) child.kill();
    if (stopped) await stopped;
    if (proxy?.listening) await new Promise(resolve => proxy.close(resolve));
    await fs.rm(temp, { recursive: true, force: true, maxRetries: 3 });
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
