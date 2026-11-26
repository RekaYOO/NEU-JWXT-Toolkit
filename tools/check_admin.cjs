const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

(async () => {
  const build = path.resolve(process.env.QA_FRONTEND_BUILD || path.join(__dirname, '../frontend/build'));
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-admin-qa-'));
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = new URL(request.url, 'http://localhost').pathname;
      let file = path.resolve(build, `.${pathname}`);
      if (file !== build && !file.startsWith(build + path.sep)) return response.writeHead(403).end();
      if (!path.extname(pathname)) file = path.join(build, 'index.html');
      response.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream');
      response.end(await fs.readFile(file));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
    for (const width of [320, 375, 430, 768, 1024, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
      let access = false;
      let schoolAccount = '20990001';
      let enabled = true;
      const writes = [];
      const unexpected = [];
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: true, configured: true, authenticated: access };
        else if (url.pathname === '/api/access/login') { access = true; data = { success: true }; }
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'server' }, auth: { is_logged_in: true, current_user: schoolAccount } };
        else if (url.pathname === '/api/admin/status') data = { eligible: schoolAccount === '20990001' };
        else if (url.pathname === '/api/admin/appearance' && route.request().method() === 'GET' && schoolAccount === '20990001') data = { site_default: 'sakura' };
        else if (url.pathname === '/api/admin/accounts') {
          assert.equal(schoolAccount, '20990001');
          data = { deployment_mode: 'remote-multi', default_administrator: '20990001', accounts: [{ account: '20990001', enabled: true }, { account: '20990002', enabled }] };
        }
        else if (['/api/system-settings/cache', '/api/system-settings/mail', '/api/system-settings/auth-recovery', '/api/status', '/api/appearance', '/api/avatar', '/api/cache/settings', '/api/runtime', '/api/health'].includes(url.pathname)) data = {};
        else if (url.pathname === '/api/client/updates') data = { updates: [] };
        else if (url.pathname === '/api/cache/events') data = { events: [] };
        else if (url.pathname === '/api/auth/pending') data = { pending_auth: null };
        else if (['/api/user/avatar/cache', '/api/cache/refresh/avatar'].includes(url.pathname)) data = {};
        else if (/^\/api\/admin\/accounts\/20990002\/(stop|disable|remove|enable)$/.test(url.pathname)) {
          assert.equal(route.request().method(), 'POST');
          writes.push(url.pathname.split('/').pop());
          if (writes.at(-1) === 'disable') enabled = false;
          if (writes.at(-1) === 'enable') enabled = true;
          data = { success: true };
        } else { unexpected.push(url.pathname); return route.fulfill({ status: 404, json: {} }); }
        return route.fulfill({ json: data });
      });
      await page.goto(`${origin}/system-settings?tab=admin`);
      await page.getByLabel('访问密码').fill('synthetic-access-password');
      await page.getByRole('button', { name: '进入工具箱' }).click();
      await page.getByRole('heading', { name: '准入名单' }).waitFor();
      assert.equal(await page.locator('.admin-content input[type=password]').count(), 0);
      const ownerRow = page.getByRole('listitem').filter({ hasText: '20990001' });
      const memberRow = page.getByRole('listitem').filter({ hasText: '20990002' });
      await ownerRow.getByText('默认管理员', { exact: true }).waitFor();
      assert.equal(await ownerRow.getByRole('button', { name: '停用账号', exact: true }).count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await memberRow.getByRole('button', { name: '停止运行', exact: true }).click();
      assert.deepEqual(writes, []);
      await page.getByRole('button', { name: /取\s*消/ }).click();
      assert.deepEqual(writes, []);
      await page.getByRole('button', { name: '停用账号', exact: true }).click();
      await page.getByRole('button', { name: /确\s*认/ }).click();
      await page.getByText('准入已停用', { exact: true }).waitFor();
      assert.deepEqual(writes, ['disable']);
      await page.getByRole('button', { name: '恢复准入', exact: true }).click();
      await page.getByRole('button', { name: /确\s*认/ }).click();
      await memberRow.getByText('准入已启用', { exact: true }).waitFor();
      assert.deepEqual(writes, ['disable', 'enable']);
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.screenshot({ path: path.join(screenshots, `admin-settings-${width}.png`), fullPage: true });
      schoolAccount = '20990002';
      await page.goto(`${origin}/system-settings?tab=admin`);
      await page.getByText('应用版本', { exact: true }).waitFor();
      assert.equal(await page.getByRole('tab', { name: '站点管理' }).count(), 0);
      assert.deepEqual(unexpected, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(`PASS account-bound settings without administrator password across six viewports; screenshots: ${screenshots}`);
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
