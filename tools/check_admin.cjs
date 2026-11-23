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
      let admin = false;
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
        else if (url.pathname === '/api/admin/status') data = { configured: true, authenticated: admin, deployment_mode: 'remote-multi' };
        else if (url.pathname === '/api/admin/login') { admin = true; data = { success: true }; }
        else if (url.pathname === '/api/admin/logout') { admin = false; data = { success: true }; }
        else if (url.pathname === '/api/admin/appearance' && route.request().method() === 'GET' && admin) data = { site_default: 'sakura' };
        else if (url.pathname === '/api/admin/accounts') data = { accounts: [{ account: '20990001', enabled }] };
        else if (/^\/api\/admin\/accounts\/20990001\/(stop|disable|remove|enable)$/.test(url.pathname)) {
          assert.equal(route.request().method(), 'POST');
          writes.push(url.pathname.split('/').pop());
          if (writes.at(-1) === 'disable') enabled = false;
          if (writes.at(-1) === 'enable') enabled = true;
          data = { success: true };
        } else { unexpected.push(url.pathname); return route.fulfill({ status: 404, json: {} }); }
        return route.fulfill({ json: data });
      });
      await page.goto(`${origin}/admin`);
      await page.getByLabel('访问密码').fill('synthetic-access-password');
      await page.getByRole('button', { name: '进入工具箱' }).click();
      await page.getByLabel('管理员密码').fill('synthetic-admin-password');
      await page.getByRole('button', { name: '进入管理' }).click();
      await page.getByText('20990001', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await page.getByRole('button', { name: '停止运行', exact: true }).click();
      assert.deepEqual(writes, []);
      await page.getByRole('button', { name: /取\s*消/ }).click();
      assert.deepEqual(writes, []);
      await page.getByRole('button', { name: '停用账号', exact: true }).click();
      await page.getByRole('button', { name: /确\s*认/ }).click();
      await page.getByText('准入已停用', { exact: true }).waitFor();
      assert.deepEqual(writes, ['disable']);
      await page.getByRole('button', { name: '恢复准入', exact: true }).click();
      await page.getByRole('button', { name: /确\s*认/ }).click();
      await page.getByText('准入已启用', { exact: true }).waitFor();
      assert.deepEqual(writes, ['disable', 'enable']);
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.screenshot({ path: path.join(screenshots, `admin-${width}.png`), fullPage: true });
      await page.getByRole('button', { name: '退出管理', exact: true }).click();
      await page.getByLabel('管理员密码').waitFor();
      assert.equal(await page.getByText('20990001', { exact: true }).count(), 0);
      assert.deepEqual(unexpected, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(`PASS admin gateway, confirmation, disable/restore, logout, six viewports; screenshots: ${screenshots}`);
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
