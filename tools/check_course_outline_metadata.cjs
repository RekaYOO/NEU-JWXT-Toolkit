// Anonymous browser regression against the production build; no backend or school access.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const build = path.resolve(__dirname, '../frontend/build');
const courses = ['A100', 'B200'].map((code, index) => ({
  course_code: code, course_name: `测试课程${index + 1}`, credit: 2,
  course_nature: '必修', term_code: '2026-2027-1', status: '未修读',
  is_passed: false, is_selected: false, category_path: ['必修'],
}));
const report = {
  categories: [{
    wid: 'fixture', name: '必修', path: '必修', path_array: ['必修'],
    requirement_type: 'required', required_credits: 4, earned_credits: 0,
    remaining_credits: 4, children: [], courses,
  }],
  credit_summary: {},
  cache: { revision: 'fixture', is_stale: false },
};
const auth = { is_logged_in: true, current_user: 'outline-fixture' };
const saved = code => ({
  course_code: code, assessment_method: '考试',
  grading_scale: code === 'A100' ? '百分制' : '五级制', status: 'success', needs_sync: false,
});

(async () => {
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-outline-qa-'));
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const candidate = path.resolve(build, `.${pathname}`);
      if (candidate !== build && !candidate.startsWith(`${build}${path.sep}`)) {
        response.writeHead(403).end();
        return;
      }
      const filename = pathname === '/' ? path.join(build, 'index.html') : candidate;
      const type = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
        '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(filename)];
      response.setHeader('Content-Type', type || 'application/octet-stream');
      response.end(await fs.readFile(filename));
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
    for (const [width, height] of [[320, 800], [375, 812], [430, 932], [768, 900], [1440, 900]]) {
      const page = await browser.newPage({ viewport: { width, height }, serviceWorkers: 'block' });
      const pageErrors = [];
      let reads = 0;
      let submissions = 0;
      let completed = false;
      let initialReadSeen = false;
      page.on('pageerror', error => pageErrors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/academic-report'));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/academic-report/cache') data = report;
        else if (url.pathname === '/api/user/avatar') return route.fulfill({ status: 204 });
        else if (url.pathname === '/api/course-outlines/metadata/read') {
          reads += 1;
          initialReadSeen = true;
          data = { items: completed ? [saved('A100'), saved('B200')] : [saved('A100')] };
        } else if (url.pathname === '/api/course-outlines/metadata/sync/status') {
          assert(initialReadSeen, 'status must not block the first cache read');
          if (submissions) completed = true;
          data = { running: false, total: submissions ? 1 : 0, completed: submissions ? 1 : 0, failed: 0, errors: [] };
        } else if (url.pathname === '/api/course-outlines/metadata/sync') {
          submissions += 1;
          assert.deepEqual(route.request().postDataJSON().courses.map(course => course.course_code), ['B200']);
          data = { running: true, accepted: true, total: 1, completed: 0, failed: 0, errors: [] };
        }
        return route.fulfill({ json: data });
      });
      await page.goto(`${origin}/`);
      await page.getByText('测试课程1', { exact: true }).first().waitFor();
      if (width >= 768) {
        const cached = page.locator('tr.ant-table-row').filter({ hasText: '测试课程1' });
        assert((await cached.textContent()).includes('百分制'), 'cached scale should render immediately');
        assert(!(await cached.textContent()).includes('加载中'), 'cached row should not show loading');
      }
      // The actual 5-second monitor must finish and read one terminal snapshot.
      await page.waitForResponse(response => (
        response.url().endsWith('/metadata/read') && reads >= 2
      ));
      await page.waitForFunction(() => !document.body.textContent.includes('加载中'));
      assert.equal(submissions, 1);
      assert.equal(reads, 2, 'metadata must not be fetched on every status tick');
      const bounds = await page.evaluate(() => ({
        width: innerWidth, scroll: document.documentElement.scrollWidth,
      }));
      assert(bounds.scroll <= bounds.width + 1, `page overflow at ${width}px`);
      await page.screenshot({ path: path.join(screenshots, `${width}.png`), fullPage: true });
      if (width < 768) await page.setViewportSize({ width: 1440, height: 900 });
      await page.getByText('五级制', { exact: true }).first().waitFor();
      assert.deepEqual(pageErrors, []);
      console.log(`PASS cached-first and terminal metadata ${width}x${height}`);
      await page.close();
    }
    console.log(`Screenshots: ${screenshots}`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
