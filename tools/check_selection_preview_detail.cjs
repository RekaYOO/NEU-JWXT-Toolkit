// Production UI regression. All APIs are intercepted with anonymous fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const build = path.resolve(__dirname, '../frontend/build');
const term = { code: '2026-2027-1', name: '测试学期', current: true };
const batch = { code: 'fixture', name: '测试轮次', state: 'ended', term_code: term.code, selection_type_code: '02' };
const course = { class_id: 'class-fixture', course_code: 'C1', course_name: '测试数据分析课程',
  teaching_class_type: 'XGKC', teacher: '测试教师', location: '教学楼101',
  schedules: [{ weekday: 7, start_section: 1, end_section: 2, weeks: [1, 2], location: '教学楼101' }] };
const metadata = { course_nature: '通识选修', exam_type: '考查', score_scale: '百分制' };
const group = { group_id: 'C1', course_code: 'C1', course_name: course.course_name, class_count: 1, classes: [course] };
const personal = { term_code: term.code, campuses: [{ code: '00', name: '南湖校区' }],
  weeks: [{ number: 1, name: '第1周', current: true }],
  sections: [{ number: 1, name: '第1节' }, { number: 2, name: '第2节' }],
  courses: [], unscheduled: [], practices: [], is_fresh: true };

(async () => {
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-preview-detail-'));
  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      const filename = url.pathname === '/' ? path.join(build, 'index.html')
        : path.resolve(build, `.${decodeURIComponent(url.pathname)}`);
      if (!filename.startsWith(`${build}${path.sep}`)) return response.writeHead(403).end();
      response.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript',
        '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(filename)] || 'application/octet-stream');
      response.end(await fs.readFile(filename));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    for (const width of [320, 375, 430, 768, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
      const errors = [];
      let detailCalls = 0;
      let release;
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/course-selection/fixture'));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: 'fixture' };
        let data = {};
        if (url.pathname === '/api/client/bootstrap') data = { auth, runtime: { profile: 'development' },
          timetable: { terms: [term], current: term.code, personal: [personal] } };
        else if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/user/avatar') return route.fulfill({ status: 204 });
        else if (url.pathname.startsWith('/api/timetable/')) data = url.pathname.endsWith('/personal')
          ? personal : { terms: [term], current: term.code, personal: [personal], jobs: [] };
        else if (url.pathname.endsWith('/jwxk/status')) data = { batches: [batch], service_authenticated: true };
        else if (url.pathname.endsWith('/plan/read')) data = { batch, batch_code: 'fixture', items: [], groups: [] };
        else if (url.pathname.endsWith('/selected')) data = { selected: [], volunteered: [] };
        else if (url.pathname.endsWith('/catalog/search')) data = { groups: [group], total: 1, cache_hit: true, scope: 'ALL' };
        else if (url.pathname.endsWith('/catalog/detail')) {
          detailCalls += 1;
          assert.equal(route.request().postDataJSON().class_id, course.class_id);
          await new Promise(resolve => { release = resolve; });
          data = { course: { ...course, ...metadata }, teaching_class: course };
        }
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      await page.locator('.jwxk-course-group').first().click();
      await page.getByRole('button', { name: '在课表中预览', exact: true }).click();
      const preview = page.locator('.timetable-query-results .is-selection-preview:visible').first();
      await preview.click();
      await page.getByText('正在读取课程详情…', { exact: true }).waitFor();
      assert.equal(detailCalls, 1);
      const property = label => page.locator('.timetable-course-detail:visible .ant-descriptions-row').filter({ hasText: label });
      assert(!(await property('课程性质').innerText()).includes('正在预览'));
      assert(release);
      release();
      await page.waitForFunction(() => [...document.querySelectorAll('.timetable-course-detail .ant-descriptions-row')]
        .some(row => row.textContent.includes('考核方式') && row.textContent.includes('考查')));
      assert((await property('课程性质').innerText()).includes('通识选修'));
      assert((await property('成绩类型').innerText()).includes('百分制'));
      assert.equal(await page.locator('.timetable-course-detail:visible').getByText('正在预览', { exact: true }).count(), 1);
      await page.screenshot({ path: path.join(screenshots, `${width}-detail.png`), animations: 'disabled' });
      const bounds = await page.locator('.timetable-course-detail:visible').boundingBox();
      assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
      assert.deepEqual(errors, []);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      console.log(`PASS ${width}: preview metadata, lazy detail, separate status, no overflow`);
      await page.close();
    }
    console.log(`Screenshots: ${screenshots}`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
