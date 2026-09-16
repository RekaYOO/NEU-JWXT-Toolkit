// Anonymous browser regression against a development server; no school requests.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const origin = process.env.QA_ORIGIN || 'http://localhost:3010';
const term = { code: '2026-2027-1', name: '2026-2027学年秋季学期', current: true };
const weeks = [2, 3].map((number, index) => ({ number, name: `第${number}周`, current: number === 2,
  start_date: `2026-09-${13 + index * 7}`, end_date: `2026-09-${19 + index * 7}` }));
const courses = [{ id: 'course-a', course_name: '源日课程', weekday: 7, weeks: [3], start_section: 1,
  end_section: 2, start_time: '08:00', end_time: '09:40', teachers: ['测试教师'], location: '教学楼101', course_nature: '必修', assessment_type: '考试' },
  { id: 'course-b', course_name: '本周课程', weekday: 3, weeks: [2], start_section: 3, end_section: 4,
    start_time: '10:00', end_time: '11:40', teachers: ['测试教师'], location: '教学楼102' }];
const personal = { term_code: term.code, campuses: [{ code: '00', name: '南湖校区' }], weeks,
  sections: [], sections_by_campus: {}, courses, unscheduled: [], practices: [], is_fresh: true };

async function checkBounds(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'page overflow');
  const bad = await page.locator('.timetable-day-agenda-modal .ant-modal-content').evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const visible = [...element.querySelectorAll('input, textarea, button, .ant-select')]
      .filter(item => item.getBoundingClientRect().width > 0);
    return visible.filter(item => {
      const rect = item.getBoundingClientRect();
      return rect.left < bounds.left - 1 || rect.right > bounds.right + 1;
    }).map(item => item.outerHTML.slice(0, 140));
  });
  assert.deepEqual(bad, [], 'form control overflow');
}

(async () => {
  const screenshots = path.resolve(__dirname, '../.artifacts/timetable-day-agenda');
  await fs.mkdir(screenshots, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
  try {
    for (const [width, height] of [[320, 800], [375, 812], [430, 932], [768, 900], [1440, 900]]) {
      for (const compact of width < 992 ? [false, true] : [false]) {
        let document = { revision: 0, events: [], moves: [] };
        const errors = [];
        const requests = [];
        const page = await browser.newPage({ viewport: { width, height }, serviceWorkers: 'block' });
        await page.clock.install({ time: new Date('2026-09-16T08:00:00+08:00') });
        await page.addInitScript(({ compact }) => localStorage.setItem('neu_toolbox:mobileCompactWeekView', JSON.stringify(compact)), { compact });
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', async route => {
          const url = new URL(route.request().url());
          const allowed = [new URL(origin).origin, 'http://localhost:8000', 'http://127.0.0.1:8000'];
          if (!allowed.includes(url.origin)) return route.abort();
          if (!url.pathname.startsWith('/api/')) return url.origin === new URL(origin).origin ? route.continue() : route.abort();
          requests.push(url.pathname);
          let data = {};
          const auth = { is_logged_in: true, current_user: 'agenda-fixture' };
          const timetable = { terms: [term], current: term.code, personal: [personal] };
          if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
          else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth, timetable };
          else if (url.pathname === '/api/status') data = auth;
          else if (['/api/timetable/terms', '/api/timetable/bootstrap'].includes(url.pathname)) data = timetable;
          else if (url.pathname === '/api/timetable/personal') data = personal;
          else if (url.pathname === '/api/timetable/agenda') {
            if (route.request().method() === 'PUT') document = { ...route.request().postDataJSON(), revision: document.revision + 1 };
            data = document;
          } else if (url.pathname === '/api/timetable/sync') data = { jobs: [] };
          else if (url.pathname === '/api/user/avatar') return route.fulfill({ status: 204 });
          return route.fulfill({ json: data });
        });
        await page.goto(`${origin}/timetable?week=2`);
        try { await page.locator('.timetable-page').waitFor({ state: 'attached' }); }
        catch (error) {
          console.error({ errors, pageText: await page.locator('body').innerText() });
          await page.screenshot({ path: path.join(screenshots, `${width}-startup-error.png`) });
          throw error;
        }
        async function openThursday() {
          if (width < 992 && !compact) {
            const thursday = page.locator('.timetable-mobile-day-selector .ant-segmented-item').filter({ hasText: '周四' });
            await thursday.click();
            await page.waitForTimeout(200);
            assert.equal(await page.locator('.timetable-day-agenda-modal .ant-modal-content').isVisible(), false, 'switching day must not open agenda');
            assert.equal(await thursday.locator('input').isChecked(), true, 'first click selects Thursday');
            await thursday.click();
          } else await page.getByRole('button', { name: '查看星期四当日日程', exact: true }).click();
        }
        try { await openThursday(); }
        catch (error) {
          console.error({ errors, requests, pageText: await page.locator('body').innerText() });
          await page.screenshot({ path: path.join(screenshots, `${width}-open-error.png`) });
          throw error;
        }
        const modal = page.locator('.timetable-day-agenda-modal .ant-modal-content');
        const confirm = async text => {
          const popup = page.locator('.ant-popconfirm:not(.ant-popover-hidden)').filter({ hasText: text });
          await popup.getByRole('button', { name: /确\s*定/ }).click();
          await popup.waitFor({ state: 'hidden' });
        };
        await modal.getByText('2026年9月17日 · 星期四').waitFor();
        await modal.getByRole('button', { name: '添加日程' }).click();
        await modal.getByLabel('标题', { exact: true }).fill('学期项目讨论');
        await modal.getByLabel('地点', { exact: true }).fill('教学楼 A101 · 长地点名称用于窄屏检查');
        await modal.getByLabel('备注', { exact: true }).fill('携带项目资料和笔记。'.repeat(8));
        await modal.getByLabel('重要信息', { exact: true }).fill('项目汇报 · 重要');
        await modal.getByLabel('开始时间', { exact: true }).click();
        const timePopup = page.locator('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden)');
        await timePopup.waitFor();
        const popupRect = await timePopup.boundingBox();
        assert(popupRect.x >= -1 && popupRect.x + popupRect.width <= width + 1, 'time popup overflow');
        await page.keyboard.press('Escape');
        await timePopup.waitFor({ state: 'hidden' });
        await checkBounds(page);
        await page.screenshot({ path: path.join(screenshots, `${width}-${compact}-form.png`) });
        await modal.getByRole('button', { name: '保存日程' }).click();
        await modal.getByRole('button', { name: '编辑学期项目讨论', exact: true }).waitFor();
        assert.equal(document.events.length, 1);
        const originalId = document.events[0].id;
        await modal.getByRole('button', { name: '修改日程：学期项目讨论', exact: true }).click();
        assert.equal(await modal.getByLabel('地点', { exact: true }).inputValue(), '教学楼 A101 · 长地点名称用于窄屏检查');
        await modal.getByLabel('地点', { exact: true }).fill('教学楼 A202 · 已修改');
        await modal.getByLabel('备注', { exact: true }).fill('修改后的备注');
        await modal.getByRole('button', { name: '保存日程' }).click();
        await modal.getByText('教学楼 A202 · 已修改', { exact: true }).waitFor();
        assert.equal(document.events.length, 1);
        assert.equal(document.events[0].id, originalId);
        assert.equal(document.events[0].note, '修改后的备注');
        await modal.getByRole('button', { name: '编辑学期项目讨论', exact: true }).click();
        await modal.getByLabel('标题', { exact: true }).fill('取消修改');
        await modal.getByRole('button', { name: /取\s*消/ }).click();
        assert.equal(document.events[0].title, '学期项目讨论');
        const datePicker = modal.locator('.day-agenda-move-controls input');
        await datePicker.click();
        const datePopup = page.locator('.day-agenda-date-popup:not(.ant-picker-dropdown-hidden)');
        await datePopup.waitFor();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const dateRect = await datePopup.boundingBox();
        if (dateRect.x < -1 || dateRect.x + dateRect.width > width + 1) {
          await page.screenshot({ path: path.join(screenshots, `${width}-date-error.png`) });
        }
        assert(dateRect.x >= -1 && dateRect.x + dateRect.width <= width + 1, `date popup overflow: ${JSON.stringify(dateRect)}`);
        await page.locator('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden) td[title="2026-09-20"]').click();
        await modal.getByRole('button', { name: '复制课程' }).click();
        await confirm('原日期不变');
        await modal.getByText('源日课程', { exact: true }).waitFor();
        assert.deepEqual(document.moves, [{ source: '2026-09-20', target: '2026-09-17' }]);
        await checkBounds(page);
        await page.screenshot({ path: path.join(screenshots, `${width}-${compact}-list.png`) });
        await modal.getByRole('button', { name: '撤销调休' }).click();
        await confirm('移除当天复制');
        await modal.getByText('源日课程', { exact: true }).waitFor({ state: 'hidden' });
        assert.equal(document.moves.length, 0);
        await modal.getByRole('button', { name: '删除学期项目讨论', exact: true }).click();
        await confirm('删除这条日程');
        await modal.getByRole('button', { name: '编辑学期项目讨论', exact: true }).waitFor({ state: 'hidden' });
        assert.equal(document.events.length, 0);
        assert.deepEqual(errors, []);
        console.log(`PASS ${width}x${height} compact=${compact}`);
        await page.close();
      }
    }
    console.log(`Screenshots: ${screenshots}`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
