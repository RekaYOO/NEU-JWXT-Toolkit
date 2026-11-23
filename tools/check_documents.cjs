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
  let activePreview;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
    for (const width of [320, 375, 768, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
      const page = await context.newPage();
      activePage = page;
      let generations = 0;
      const fixtureBytes = Buffer.from('%PDF-1.4\nsynthetic transport fixture\n%%EOF\n');
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/export/academic-documents'));
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: '20990001' };
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/export/academic-documents') data = { groups: [{ name: '成绩单', documents: [{ id: 'document', name: '中文成绩单', semester_limit: 50 }] }] };
        else if (url.pathname.endsWith('/academic-documents/generate')) {
          generations++;
          assert.deepEqual(route.request().postDataJSON(), { document_id: 'document' });
          if (generations === 2) return route.fulfill({ status: 503, json: { detail: '模拟报告暂不可用' } });
          if (generations === 3) return route.fulfill({ headers: { 'content-type': 'application/pdf', 'content-disposition': "attachment; filename*=UTF-8''report.pdf" }, body: fixtureBytes });
          return route.fulfill({ headers: { 'content-type': 'text/html; charset=utf-8', 'x-academic-document-format': 'html', 'content-disposition': "inline; filename*=UTF-8''report.html" }, body: '<html><head><meta charset="utf-8"></head><body><h1>模拟官方报告</h1><script>try { parent.document.body; document.body.dataset.isolated="no"; } catch { document.body.dataset.isolated="yes"; }</script></body></html>' });
        } else if (url.pathname.includes('avatar')) return route.fulfill({ status: 204, body: '' });
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      const generate = page.getByRole('button', { name: /生成证明/ });
      await generate.click();
      await page.getByRole('button', { name: /^取\s*消$/ }).click();
      assert.equal(generations, 0);
      await generate.click();
      const popupPromise = context.waitForEvent('page');
      await page.getByRole('button', { name: '确认生成', exact: true }).click();
      const preview = await popupPromise;
      activePreview = preview;
      await preview.frameLocator('iframe').getByText('模拟官方报告').waitFor();
      assert.equal(await preview.frameLocator('iframe').locator('body').getAttribute('data-isolated'), 'yes');
      assert.equal(await preview.evaluate(() => window.opener === null), true);
      assert.equal(generations, 1);
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await preview.close();
      await generate.click();
      await page.getByRole('button', { name: '确认生成', exact: true }).click();
      await page.getByText('模拟报告暂不可用', { exact: true }).waitFor();
      assert.equal(generations, 2);
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await generate.click();
      const downloaded = page.waitForEvent('download');
      await page.getByRole('button', { name: '确认生成', exact: true }).click();
      const file = await downloaded;
      assert.equal(file.suggestedFilename(), 'report.pdf');
      assert.deepEqual(await fs.readFile(await file.path()), fixtureBytes);
      assert.equal(generations, 3);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log(JSON.stringify({ passed: true, widths: [320, 375, 768, 1440], htmlIsolation: true }));
  } catch (error) {
    if (activePage && !activePage.isClosed()) console.error(await activePage.locator('body').innerText());
    if (activePreview && !activePreview.isClosed()) console.error(await activePreview.content(), activePreview.frames().map(frame => frame.url()));
    throw error;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
