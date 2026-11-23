// Production UI regression. All APIs are intercepted with anonymous fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

const build = path.resolve(process.env.QA_FRONTEND_BUILD || path.join(__dirname, '../frontend/build'));
const term = { code: '2026-2027-1', name: '测试学期', current: true };
const previousTerm = { code: '2025-2026-2', name: '历史测试学期' };
const batch = { code: 'fixture', name: '测试轮次', state: 'ended', term_code: term.code, selection_type_code: '02' };
const course = { class_id: 'class-fixture', course_code: 'C1', course_name: '测试数据分析课程',
  teaching_class_type: 'XGKC', teacher: '测试教师', location: '教学楼101',
  schedules: [{ weekday: 7, start_section: 1, end_section: 2, weeks: [1, 2], location: '教学楼101' }],
  has_experiment: true, experiment_hours: 8,
  experiment_schedules: [{
    schedule_id: 'exp-fixture', project_name: '体验测试实验',
    raw_text: '13周 星期四 第七节-第八节', weekday: 4, start_section: 7, end_section: 8,
    weeks: [13], location: '实验中心A101', teacher: '测试教师', group_name: '实验组 1',
  }] };
const metadata = { course_nature: '通识选修', exam_type: '考查', score_scale: '百分制' };
const group = { group_id: 'C1', course_code: 'C1', course_name: course.course_name, class_count: 1, classes: [course] };
const personal = { term_code: term.code, campuses: [{ code: '00', name: '南湖校区' }],
  weeks: Array.from({ length: 13 }, (_, index) => ({ number: index + 1, name: `第${index + 1}周`, current: index === 0 })),
  sections: Array.from({ length: 8 }, (_, index) => ({ number: index + 1, name: `第${index + 1}节` })),
  courses: [], unscheduled: [], practices: [], is_fresh: true };
const previousPersonal = { ...personal, term_code: previousTerm.code, courses: [{ ...course,
  id: 'historic-course', weekday: 2, start_section: 3, end_section: 4, weeks: [1], location: '历史教室203' }] };
const timetable = { terms: [term, previousTerm], current: term.code, personal: [personal, previousPersonal] };

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
    browser = await chromium.launch({ channel: process.env.QA_BROWSER || 'msedge', headless: true });
    for (const width of [320, 375, 430, 768, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
      const errors = [];
      let detailCalls = 0;
      const planSaves = [];
      const enrollmentRequests = [];
      let outlineVisits = 0;
      const outlineGroups = [];
      let resolvePlanSave;
      const planSaveObserved = new Promise(resolve => { resolvePlanSave = resolve; });
      let release;
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/course-selection/fixture'));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        if (/^\/api\/course-selection\/(?:jwxk\/courses\/(?:select|deselect)|official-withdrawal\/deselect)$/.test(url.pathname)) enrollmentRequests.push(url.pathname);
        const auth = { is_logged_in: true, current_user: 'fixture' };
        let data = {};
        if (url.pathname === '/api/client/bootstrap') data = { auth, runtime: { profile: 'development' },
          timetable };
        else if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/course-outlines/detail/overview') {
          assert.equal(route.request().postDataJSON().course_code, course.course_code);
          data = { course_name: course.course_name, introduction: `大纲读取版本 ${++outlineVisits}` };
        } else if (url.pathname === '/api/course-outlines/detail/sections') {
          const request = route.request().postDataJSON();
          assert.equal(request.course_code, course.course_code);
          outlineGroups.push([outlineVisits, request.group]);
          data = { sections: [{ kind: 'attachments', title: `已读取 ${outlineVisits} ${request.group}`, items: [] }] };
        }
        else if (url.pathname === '/api/user/avatar') return route.fulfill({ status: 204 });
        else if (['/api/timetable/context', '/api/timetable/schedule'].includes(url.pathname)) {
          const fixture = route.request().postDataJSON().term_code === previousTerm.code ? previousPersonal : personal;
          data = { ...fixture, mode: 'personal', campus_code: '00' };
        }
        else if (url.pathname.startsWith('/api/timetable/')) data = url.pathname.endsWith('/personal')
          ? (url.search.includes(previousTerm.code) || route.request().postData()?.includes(previousTerm.code) ? previousPersonal : personal) : { ...timetable, jobs: [] };
        else if (url.pathname.endsWith('/jwxk/status')) data = { batches: [batch], service_authenticated: true };
        else if (url.pathname.endsWith('/plan/read')) data = {
          batch, batch_code: 'fixture', items: [], groups: [], experiment_selections: {},
        };
        else if (url.pathname.endsWith('/plan/save')) {
          planSaves.push(route.request().postDataJSON());
          resolvePlanSave();
          data = { batch_code: 'fixture', items: [], groups: [], experiment_selections: planSaves.at(-1).experiment_selections };
        }
        else if (url.pathname.endsWith('/selected')) data = { selected: [], volunteered: [] };
        else if (url.pathname.endsWith('/catalog/search')) data = { groups: [group], total: 1, cache_hit: true, scope: 'ALL' };
        else if (url.pathname.endsWith('/catalog/detail')) {
          detailCalls += 1;
          assert.equal(route.request().postDataJSON().class_id, course.class_id);
          if (detailCalls === 1) await new Promise(resolve => { release = resolve; });
          data = {
            course: { ...course, ...metadata }, teaching_class: course,
            programs: [{
              training_code: 'fixture-program', grade: '2024', program_name: '2024 测试方案',
              module_name: '通识选修模块', course_category: '通识选修', course_nature: '选修',
            }],
            programs_loaded: true,
          };
        }
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      await page.locator('.jwxk-course-group').first().waitFor();
      if (width >= 992) {
        const controls = page.locator('.timetable-desktop-controls');
        await controls.getByText('全学期', { exact: true }).click();
        const actionGap = await controls.evaluate(element => {
          const bounds = element.getBoundingClientRect();
          const refresh = element.querySelector('.timetable-refresh-button').getBoundingClientRect();
          return bounds.right - refresh.right;
        });
        assert(Math.abs(actionGap) < 2, `embedded timetable refresh is not right-aligned (${actionGap}px)`);
      }
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
      const lazyDetailContainer = page.locator('.ant-modal:has(.timetable-course-detail):visible, .ant-drawer:has(.timetable-course-detail):visible');
      await lazyDetailContainer.locator('.ant-modal-close, .ant-drawer-close').click();
      await page.locator('.timetable-course-detail:visible').waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: '查看详情', exact: true }).click();
      const courseDetail = page.locator('.jwxk-catalog-detail-modal:visible');
      await courseDetail.getByText('所属方案', { exact: true }).waitFor();
      for (const visit of [1, 2]) {
        await courseDetail.getByRole('tab', { name: '课程大纲' }).click();
        await courseDetail.getByText(`大纲读取版本 ${visit}`, { exact: true }).waitFor();
        for (const groupName of ['teaching', 'assessment', 'governance']) {
          await courseDetail.getByText(`已读取 ${visit} ${groupName}`, { exact: true }).waitFor();
        }
        assert.deepEqual(outlineGroups.filter(([revision]) => revision === visit).map(([, groupName]) => groupName), ['teaching', 'assessment', 'governance']);
        if (visit === 2) {
          const downloaded = page.waitForEvent('download');
          await courseDetail.getByRole('button', { name: '下载大纲' }).click();
          const file = await downloaded;
          assert.match(file.suggestedFilename(), /课程大纲\.html$/);
          const html = await fs.readFile(await file.path(), 'utf8');
          for (const marker of ['大纲读取版本', '已读取']) {
            assert(!html.includes(`${marker} 1`));
          }
          for (const marker of ['大纲读取版本 2', '已读取 2 teaching', '已读取 2 assessment', '已读取 2 governance']) {
            assert(html.includes(marker), `missing exported outline content: ${marker}`);
          }
        }
        await courseDetail.getByRole('tab', { name: '课程详情' }).click();
      }
      assert.equal(await courseDetail.getByText('2024 测试方案', { exact: true }).count(), 1);
      await courseDetail.getByText('实验课表', { exact: true }).waitFor();
      const experimentSelect = courseDetail.locator('.jwxk-experiment-project .ant-select').first();
      await experimentSelect.click();
      await page.locator('.ant-select-dropdown:visible').getByText(/13周 星期四 第七节-第八节/).click();
      await planSaveObserved;
      assert(planSaves.some(payload => JSON.stringify(payload.experiment_selections) === JSON.stringify({ 'class-fixture': ['exp-fixture'] })), 'experiment selection was not persisted');
      await page.screenshot({ path: path.join(screenshots, `${width}-detail.png`), animations: 'disabled' });
      const bounds = await courseDetail.boundingBox();
      assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
      await courseDetail.locator('.ant-modal-close').click();
      if (width < 992) {
        await page.locator('[data-week="13"]').click();
      }
      await page.getByText('测试数据分析课程 · 体验测试实验', { exact: true }).and(page.locator(':visible')).first().waitFor();
      if (width >= 992) {
        await page.locator('.timetable-desktop-controls .ant-select').first().click();
        await page.locator('.ant-select-dropdown:visible').getByText(previousTerm.name, { exact: true }).click();
        const historicalCourse = page.locator('.timetable-query-results').getByText(course.course_name, { exact: true }).and(page.locator(':visible')).first();
        await historicalCourse.click();
        const detail = page.locator('.timetable-course-detail:visible');
        await detail.getByText('历史教室203', { exact: false }).first().waitFor();
        assert.equal(await detail.getByText('正在预览', { exact: true }).count(), 0);
        await lazyDetailContainer.locator('.ant-modal-close, .ant-drawer-close').click();
        await detail.waitFor({ state: 'hidden' });
        await page.locator('.timetable-desktop-controls .ant-select').first().click();
        await page.locator('.ant-select-dropdown:visible').getByText(`${term.name}（当前）`, { exact: true }).click();
        await preview.click();
        await detail.getByText('正在预览', { exact: true }).waitFor();
        assert.equal(await detail.getByText('历史教室203', { exact: false }).count(), 0);
      }
      assert.deepEqual(errors, []);
      assert.deepEqual(enrollmentRequests, []);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      console.log(`PASS ${width}: preview metadata, catalog detail, programs, experiment save, no overflow`);
      await page.close();
    }
    console.log(`Screenshots: ${screenshots}`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
