const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

(async () => {
  const build = path.resolve(process.env.QA_FRONTEND_BUILD || path.join(__dirname, '../frontend/build'));
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-experiment-qa-'));
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
    for (const width of [320, 375, 768, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
      let writes = 0;
      let verifications = 0;
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/experiment-courses'));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: '20990001' };
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/experiment-courses/cache') data = { term: '2026-2027-1', selected_results: [], courses: [{ task_id: 'task', course_name: '材料综合实验课程', course_no: 'course', selected_count: 0, must_do_count: 1, projects: [{ project_code: 'project', project_name: '材料综合实验项目', is_selected: false }] }] };
        else if (url.pathname.endsWith('/rounds')) data = { rounds: [{ wid: 'round', round_name: '材料综合实验班', can_select: true, conflict_status: 'unknown', selected_count: 0, capacity: 20 }] };
        else if (url.pathname === '/api/experiment-courses/select') { writes++; data = { code: '-1', state: 'unknown', msg: '结果尚未确认，请核验官方结果；不会重复提交。' }; }
        else if (url.pathname === '/api/experiment-courses/verify') { verifications++; data = { code: '0', state: 'confirmed', msg: '官方结果已确认' }; }
        else if (url.pathname.startsWith('/api/cache/refresh/')) data = { status: 'fresh' };
        else if (url.pathname.includes('avatar')) return route.fulfill({ status: 204, body: '' });
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      if (width < 768) await page.locator('.experiment-course-page .ant-collapse-header').click();
      else await page.locator('.experiment-course-page .ant-table-row-expand-icon').first().click();
      await page.getByRole('button', { name: /^选\s*课$/ }).click();
      await page.getByRole('button', { name: '确认后选择' }).click();
      assert.equal(writes, 0);
      await page.getByRole('button', { name: '仍要选择' }).click();
      const notice = page.locator('.experiment-operation-notice');
      await notice.waitFor();
      await page.getByRole('dialog', { name: '选择实验班', exact: true }).waitFor({ state: 'hidden' });
      await page.getByRole('dialog', { name: '课表冲突尚未完全核验', exact: true }).waitFor({ state: 'hidden' });
      assert.equal(writes, 1);
      const button = notice.getByRole('button', { name: '核验操作结果' });
      await button.scrollIntoViewIfNeeded();
      const bounds = await button.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width && bounds.height >= 44);
      await page.screenshot({ path: path.join(screenshots, `experiment-${width}.png`), fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, JSON.stringify(await page.locator('body *').evaluateAll(elements => elements.filter(element => element.getBoundingClientRect().right > window.innerWidth + 1).map(element => ({ tag: element.tagName, className: element.className, width: element.getBoundingClientRect().width })).slice(0, 20))));
      await button.click();
      await notice.waitFor({ state: 'hidden' });
      assert.equal(writes, 1);
      assert.equal(verifications, 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(JSON.stringify({ passed: true, screenshots }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
