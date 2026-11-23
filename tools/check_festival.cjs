const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

(async () => {
  const build = path.resolve(process.env.QA_FRONTEND_BUILD || path.join(__dirname, '../frontend/build'));
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
  let activePage;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.NEU_QA_BROWSER || 'chrome' });
    for (const width of [320, 375, 430, 768, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 932 }, serviceWorkers: 'block', reducedMotion: 'reduce', acceptDownloads: true });
      const page = await context.newPage();
      activePage = page;
      const operations = [];
      const errors = [];
      let archiveBody;
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/export/festival-activities'));
      await page.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: '20990001' };
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/export/festival-activities/status') data = { network_mode: 'follow', effective_network_mode: 'direct', primary_authenticated: true, service_authenticated: true, service_auth_state: 'authenticated' };
        else if (url.pathname === '/api/export/festival-activities/cache' && request.method() === 'DELETE') { operations.push('delete'); data = { success: true, deleted: true }; }
        else if (url.pathname === '/api/export/festival-activities') {
          operations.push('read');
          data = { available: true, source: 'remote', activities: [{ id: '1', section: '科普节', name: '科学探秘活动', start_time: `${new Date().toISOString().slice(0, 10)}T10:00:00`, certificate_available: true, location: '实验楼', description: '科学实验与交流' }], warnings: [], total: 1 };
        } else if (url.pathname.endsWith('/certificates/archive')) {
          operations.push('archive');
          archiveBody = request.postDataJSON();
          return route.fulfill({ headers: { 'content-type': 'application/zip', 'content-disposition': 'attachment; filename=certificates.zip', 'x-certificate-succeeded': '1', 'x-certificate-failed': '1' }, body: Buffer.from('UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==', 'base64') });
        } else if (url.pathname.includes('avatar')) return route.fulfill({ status: 204, body: '' });
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      await page.getByText('每次使用按需读取', { exact: true }).first().click();
      await page.getByRole('button', { name: /确认并读取活动/ }).click();
      await page.getByText('科学探秘活动', { exact: true }).first().waitFor();
      assert.deepEqual(operations, ['delete', 'read']);
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', { name: /打包.*证书/ }).click();
      const download = await downloadPromise;
      await page.getByText('已下载 1 张证书，1 张失败；详情见压缩包内下载说明', { exact: true }).waitFor();
      assert.equal(download.suggestedFilename(), 'certificates.zip');
      assert.deepEqual(Object.keys(archiveBody).sort(), ['end_date', 'start_date']);
      assert.match(archiveBody.start_date, /^\d{4}-\d{2}-\d{2}$/);
      assert.deepEqual(operations, ['delete', 'read', 'archive']);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log(JSON.stringify({ passed: true, widths: [320, 375, 430, 768, 1440], cacheDeletedBeforeRead: true }));
  } catch (error) {
    if (activePage && !activePage.isClosed()) console.error(await activePage.locator('body').innerText());
    throw error;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
