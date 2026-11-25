// Anonymous production-build regression. All API and external requests are intercepted.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

const build = path.resolve(process.env.QA_FRONTEND_BUILD || path.join(__dirname, '../frontend/build'));
const score = (code, gpa, credit) => ({
  code, name: `测试课程${code}`, gpa, credit, score: String(gpa * 10 + 50),
  score_value: gpa * 10 + 50, term: '2026-2027-1', term_display: '测试学期',
  course_type: '必修', course_category: '专业类', general_category: '',
  exam_type: '考试', exam_status: '', course_nature: '', is_passed: gpa > 0,
});
const scores = [score('CORE', 4, 3), score('FAIL', 0, 2), score('GE', 5, 2), score('BIN', 5, 1)];
const plan = (code, name) => ({
  course_code: code, course_name: name, credit: 2, course_nature: '选修',
  status: '未修读', is_passed: false, is_selected: false, plan_term: '2026-2027-1',
});
const report = {
  categories: [{
    wid: 'general', name: '通识类', path: '通识类', path_array: ['通识类'], courses: [],
    required_credits: 10, earned_credits: 0, remaining_credits: 10,
    children: [{
      wid: 'elective', name: '通识选修类', path: '通识类 > 通识选修类',
      path_array: ['通识类', '通识选修类'], requirement_type: 'elective',
      required_credits: 10, earned_credits: 0, remaining_credits: 10,
      children: [{
        wid: 'child', name: '科学素养类', path: '通识类 > 通识选修类 > 科学素养类',
        path_array: ['通识类', '通识选修类', '科学素养类'], requirement_type: 'elective',
        courses: [plan('PGE', '计划通识课程')], children: [],
        required_credits: 2, earned_credits: 0, remaining_credits: 2,
      }], courses: [],
    }],
  }],
  credit_summary: {},
  cache: { revision: 'report', dependency_revisions: { scores: 'scores' }, is_stale: false },
};
const scales = { CORE: '百分制', FAIL: '百分制', GE: '五级制', BIN: '两级制', PGE: '五级制' };

(async () => {
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-gpa-qa-'));
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = new URL(request.url, 'http://localhost').pathname;
      let file = path.resolve(build, `.${pathname}`);
      if (file !== build && !file.startsWith(build + path.sep)) return response.writeHead(403).end();
      if (pathname === '/' || !path.extname(pathname)) file = path.join(build, 'index.html');
      response.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript',
        '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream');
      response.end(await fs.readFile(file));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
    for (const [width, height] of [[320, 800], [375, 812], [430, 932], [768, 900], [1440, 900]]) {
      const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: 'block' });
      const page = await context.newPage();
      const errors = [];
      let mode = 'from_2025';
      let override = null;
      let savedFile = null;
      let savedFilename = '';
      let conflictNextSave = true;
      const plans = new Map();
      let failSave = false;
      let detailQueries = 0;
      const policy = () => ({
        mode, override, default_mode: 'from_2025', general_elective_codes: ['GE', 'PGE'],
        grading_scales: scales, report_available: true, missing_grading_scales: 0,
      });
      page.on('pageerror', error => errors.push(error.message));
      await page.context().addInitScript(() => history.replaceState(null, '', '/scores'));
      await page.context().route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: '20250001' };
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/gpa-policy') {
          if (route.request().method() === 'PUT') {
            if (failSave) return route.fulfill({ status: 503, json: { detail: 'fixture failure' } });
            override = route.request().postDataJSON().mode;
            mode = override || 'from_2025';
          }
          data = policy();
        } else if (url.pathname === '/api/scores/cache') {
          data = { scores, total_courses: 4, source: 'local', calculated_gpa: 2.4,
            overall_gpa: 3.75, official_major_rank: width === 375 ? null : 42,
            gpa_policy: policy(), cache: { revision: 'scores', is_stale: false } };
        } else if (url.pathname === '/api/academic-report/cache') data = report;
        else if (url.pathname === '/api/course-outlines/metadata/read') {
          data = { items: Object.entries(scales).map(([course_code, grading_scale]) => ({
            course_code, grading_scale, status: 'success', needs_sync: false,
          })) };
        } else if (url.pathname === '/api/scores/details/query') detailQueries += 1;
        else if (url.pathname === '/api/scores/details/cache') data = {
          item_scores: [{ code: 'FINAL', name: '期末', value: '90' }],
        };
        else if (url.pathname === '/api/gpa-simulation/export') {
          const submission = route.request().postDataJSON();
          if ((width !== 1440 && conflictNextSave) || plans.has(submission.filename)) {
            conflictNextSave = false;
            return route.fulfill({ status: 409, json: { detail: '方案已被其他设备更新' } });
          }
          assert.equal(submission.revision, 0);
          savedFile = submission.data;
          savedFilename = submission.filename;
          plans.set(savedFilename, savedFile);
          data = { success: true, revision: 1 };
        } else if (url.pathname === '/api/gpa-simulation/files') {
          data = Array.from(plans, ([filename, plan]) => ({ filename, revision: 1, size: 100, modified_time: '2026-09-06', stats: plan.stats }));
        } else if (url.pathname.startsWith('/api/gpa-simulation/file/')) {
          const plan = plans.get(decodeURIComponent(url.pathname.split('/').pop()));
          if (!plan) return route.fulfill({ status: 404, json: { detail: '方案不存在' } });
          data = { ...plan, _document_revision: 1 };
        }
        else if (url.pathname.includes('/user/avatar')) return route.fulfill({ status: 204 });
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      const average = page.locator('.stats-row .ant-statistic').filter({ hasText: '平均绩点' });
      await page.getByRole('button', { name: /绩点计算$/ }).waitFor().catch(async error => {
        console.log(await page.locator('body').innerText(), errors);
        throw error;
      });
      await page.waitForFunction(() => document.querySelector('.stats-row')?.textContent.includes('2.400'));
      assert((await average.textContent()).includes('2.400'));
      const officialSummary = page.locator('.official-score-summary');
      await officialSummary.getByText('3.75', { exact: true }).waitFor();
      const rankText = width === 375 ? '暂无' : '第 42 名';
      await officialSummary.getByText(rankText, { exact: true }).waitFor();
      await page.getByRole('button', { name: /绩点计算$/ }).click();
      await page.getByText('2024级及以前', { exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.stats-row')?.textContent.includes('3.375'));
      assert(await officialSummary.getByText(rankText, { exact: true }).isVisible());
      await page.getByRole('button', { name: /恢复年级默认$/ }).click();
      await page.waitForFunction(() => document.querySelector('.stats-row')?.textContent.includes('2.400'));
      failSave = true;
      await page.getByText('2024级及以前', { exact: true }).click();
      await page.getByText('保存失败，仍使用原绩点计算策略', { exact: true }).waitFor();
      assert.equal(mode, 'from_2025');
      failSave = false;
      await page.getByRole('button', { name: /完\s*成$/ }).click();
      await page.getByRole('button', { name: /GPA模拟$/ }).click();
      const simAverage = page.locator(width < 768 ? '.gpa-mobile-summary' : '.gpa-stats-row');
      await simAverage.waitFor();
      assert((await simAverage.textContent()).includes('2.4000'));
      assert.equal(await page.locator('.gpa-table th').filter({ hasText: /^成绩$/ }).count(), 0);
      const failedCourse = page.locator(width < 768 ? '.gpa-mobile-course' : '.gpa-table .ant-table-row').filter({ hasText: '测试课程FAIL' });
      const failedGpa = width < 768 ? failedCourse.locator('.gpa-mobile-course__inputs input').last() : failedCourse.locator('td').nth((await page.locator('.gpa-table .ant-table-thead th').allTextContents()).findIndex(text => text.trim() === '绩点')).locator('input');
      assert.match(await failedGpa.inputValue(), /^0(?:\.0+)?$/);
      await failedGpa.fill('');
      await failedGpa.blur();
      await simAverage.getByText('4.0000', { exact: true }).waitFor();
      if (width < 768) {
        await page.getByRole('button', { name: /更\s*多$/ }).click();
        await page.getByText('撤销上一步', { exact: true }).click();
      } else {
        await page.getByRole('button', { name: /撤\s*销$/ }).click();
      }
      await simAverage.getByText('2.4000', { exact: true }).waitFor();
      assert.match(await failedGpa.inputValue(), /^0(?:\.0+)?$/);
      if (width < 768) {
        const card = page.locator('.gpa-mobile-course').filter({ hasText: '测试课程CORE' });
        const labels = card.locator('.gpa-mobile-course__inputs label');
        assert.equal(await labels.count(), 2);
        const positions = await labels.evaluateAll(nodes => nodes.map(node => ({
          label: node.querySelector('span').getBoundingClientRect().top,
          input: node.querySelector('input').getBoundingClientRect().top,
        })));
        assert(Math.abs(positions[0].label - positions[1].label) < 1, 'labels must align');
        assert(Math.abs(positions[0].input - positions[1].input) < 1, 'inputs must align');
        const zero = page.locator('.gpa-mobile-course').filter({ hasText: '测试课程FAIL' });
        assert.equal(Number(await zero.locator('input').last().inputValue()), 0);
        await card.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(screenshots, `${width}-inputs.png`) });
        await page.getByRole('button', { name: /导\s*入$/ }).click();
        await page.getByText('从培养计划导入', { exact: true }).click();
        const planCard = page.locator('.plan-import-mobile-course').filter({ hasText: '计划通识课程' });
        await page.getByText('计划通识课程', { exact: true }).last().waitFor();
        assert((await planCard.textContent()).includes('五级制'));
        await planCard.getByRole('checkbox').check();
      } else {
        await page.getByRole('button', { name: /从计划导入$/ }).click();
        await page.getByText('计划通识课程', { exact: true }).first().waitFor();
        await page.locator('.ant-drawer-content .ant-table-row').first().getByRole('checkbox').check();
      }
      await page.locator('.ant-drawer-content').getByRole('button', { name: /导\s*入$/ }).click();
      const imported = page.locator(width < 768 ? '.gpa-mobile-course' : '.gpa-table .ant-table-row')
        .filter({ hasText: '计划通识课程' });
      await imported.waitFor();
      if (width < 768) {
        assert((await imported.textContent()).includes('五级制'));
        assert((await imported.textContent()).includes('通识选修类不计入绩点'));
        await imported.locator('input').last().fill('5');
        await imported.locator('input').last().blur();
        await page.getByRole('button', { name: /更\s*多$/ }).click();
        await page.getByText('保存模拟', { exact: true }).click();
      } else {
        const headers = await page.locator('.gpa-table .ant-table-thead th').allTextContents();
        const gpaCell = imported.locator('td').nth(headers.findIndex(text => text.trim() === '绩点'));
        await gpaCell.locator('input').fill('5');
        await gpaCell.locator('input').blur();
        await page.getByRole('button', { name: /保\s*存$/ }).first().click();
      }
      let peerFilename;
      let peerDraft;
      if (width === 1440) {
        peerFilename = 'two-window-plan';
        await page.getByRole('dialog').getByPlaceholder('输入文件名').fill(peerFilename);
        const peer = await page.context().newPage();
        await peer.goto(origin);
        await peer.getByRole('button', { name: /GPA模拟$/ }).click();
        await peer.getByRole('button', { name: /保\s*存$/ }).first().click();
        await peer.getByRole('dialog').getByPlaceholder('输入文件名').fill(peerFilename);
        await peer.getByRole('dialog').getByRole('button', { name: /保\s*存$/ }).click();
        await peer.getByText('已保存到服务器', { exact: false }).first().waitFor();
        peerDraft = structuredClone(savedFile);
        await peer.close();
      }
      await page.getByRole('dialog').getByRole('button', { name: /保\s*存$/ }).click();
      await page.getByRole('dialog').getByRole('alert').filter({ hasText: '当前草稿已保留' }).waitFor();
      await page.screenshot({ path: path.join(screenshots, `${width}-save-conflict.png`), animations: 'disabled' });
      assert.deepEqual(savedFile, peerDraft ?? null, 'conflicted save must preserve the earlier document');
      await page.getByRole('dialog').getByPlaceholder('输入文件名').fill('fixture-copy');
      await page.getByRole('dialog').getByRole('button', { name: /保\s*存$/ }).click();
      await page.waitForFunction(() => document.body.textContent.includes('已保存到服务器'));
      assert.equal(savedFile.courses.find(course => course.code === 'PGE').gpa_general_elective, true);
      assert.equal(savedFile.courses.find(course => course.code === 'PGE').gradingScale, '五级制');
      assert.equal(savedFile.courses.find(course => course.code === 'PGE').gpa, 5);
      assert.equal(savedFile.courses.find(course => course.code === 'PGE').credit, 2);
      assert.equal(savedFile.stats.weightedGPA, 2.4);
      if (peerDraft) {
        assert.deepEqual(plans.get(savedFilename), savedFile);
        assert.equal(plans.size, 2);
        assert.notDeepEqual(savedFile.courses, peerDraft.courses);
      }
      assert.equal(detailQueries, 0);
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.reload();
      await page.getByRole('button', { name: /绩点计算$/ }).click();
      await page.getByText('2024级及以前', { exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.stats-row')?.textContent.includes('3.375'));
      await page.reload();
      await page.waitForFunction(() => document.querySelector('.stats-row')?.textContent.includes('3.375'));
      await page.getByRole('button', { name: /GPA模拟$/ }).click();
      if (width < 768) {
        await page.getByRole('button', { name: /导\s*入$/ }).click();
        await page.getByText('从文件导入', { exact: true }).click();
      } else {
        await page.getByRole('button', { name: /从文件导入$/ }).click();
      }
      await page.getByRole('dialog').locator('.ant-list-item').filter({ hasText: 'fixture-copy' }).getByRole('button', { name: /导\s*入$/ }).click();
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.waitForFunction(() => (
        [...document.querySelectorAll('.gpa-mobile-summary,.gpa-stats-row')]
          .some(node => node.textContent.includes('3.7000'))
      ));
      assert.equal(mode, 'through_2024');
      assert.equal(savedFile.stats.weightedGPA, 2.4, 'saved historical summary must not override live policy');
      if (width < 768) {
        const reopened = page.locator('.gpa-mobile-course').filter({ hasText: '计划通识课程' });
        assert((await reopened.textContent()).includes('五级制'));
        await reopened.scrollIntoViewIfNeeded();
      }
      const bounds = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      assert(bounds.scroll <= bounds.width + 1, `overflow at ${width}px`);
      await page.mouse.move(0, height - 1);
      await page.locator('.ant-message-notice').last().waitFor({ state: 'hidden' });
      await page.screenshot({ path: path.join(screenshots, `${width}-simulation.png`), fullPage: true });
      assert.deepEqual(errors, []);
      console.log(`PASS GPA policy, inputs and plan snapshot ${width}x${height}`);
      await context.close();
    }
    console.log(`Screenshots: ${screenshots}`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
