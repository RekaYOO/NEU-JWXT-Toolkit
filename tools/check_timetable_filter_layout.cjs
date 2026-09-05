// Optional browser regression against a running local frontend (no school requests).
// PLAYWRIGHT_MODULE may point to an existing Playwright installation.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const term = { code: '2026-2027-1', name: '2026-2027学年秋季学期', current: true };
const auth = { is_logged_in: true, current_user: 'layout-fixture' };
const timetable = { terms: [term], current: term.code, personal: [] };
const catalog = Object.fromEntries([
  'campus', 'building', 'floor', 'room_type', 'department', 'grade', 'major', 'title',
].map(key => [key, [{ value: 'fixture', label: '测试分类' }]]));

async function mockApi(route) {
  const url = new URL(route.request().url());
  let data = {};
  if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
  else if (url.pathname === '/api/client/bootstrap') data = {
    runtime: { profile: 'development' }, auth, timetable,
  };
  else if (url.pathname === '/api/status') data = auth;
  else if (url.pathname === '/api/timetable/bootstrap' || url.pathname === '/api/timetable/terms') data = timetable;
  else if (url.pathname.endsWith('/filter-options')) data = { options: catalog, relations: [] };
  else if (url.pathname.endsWith('/targets/search')) data = {
    page: 1, total: 50, items: Array.from({ length: 50 }, (_, i) => ({
      id: `fixture-${i}`, name: `测试对象 ${i}`, details: { building: '测试楼宇', floor: '3.0' },
    })),
  };
  else if (url.pathname === '/api/timetable/context') data = { campuses: [], sections: [], weeks: [] };
  else if (url.pathname === '/api/user/avatar') return route.fulfill({ status: 204 });
  return route.fulfill({ json: data });
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
  try {
    const viewports = process.env.QA_VIEWPORTS ? JSON.parse(process.env.QA_VIEWPORTS)
      : [[320, 800], [375, 812], [430, 932], [768, 600], [1440, 900], [812, 375], [375, 320]];
    for (const [width, height] of viewports) {
      const page = await browser.newPage({ viewport: { width, height }, serviceWorkers: 'block' });
      const pageErrors = [];
      page.on('pageerror', error => pageErrors.push(error.message));
      const simulateKeyboard = width === 375 && height === 812;
      if (simulateKeyboard) await page.addInitScript(() => {
        Object.defineProperty(window, 'visualViewport', {
          configurable: true,
          value: Object.assign(new EventTarget(), {
            height: innerHeight, width: innerWidth, offsetTop: 0, offsetLeft: 0, scale: 1,
          }),
        });
      });
      // No real account data or school access; every API is isolated here.
      await page.route('**/api/**', mockApi);
      await page.goto(`${process.env.TIMETABLE_QA_URL || 'http://localhost:8000'}/timetable`);
      await page.locator('.timetable-mode-tabs').waitFor();
      for (const name of ['教室课表', '教师课表', '班级课表']) {
        const tab = page.getByRole('tab', { name, exact: true });
        if (await tab.isVisible()) await tab.click();
        else {
          await page.locator('.timetable-mode-tabs .ant-tabs-nav-more').click();
          await page.getByRole('menuitem', { name, exact: true }).click();
        }
        await page.locator('.timetable-target-search-row button').last().click();
        const modal = page.locator('.timetable-target-filter-modal .ant-modal');
        await modal.waitFor({ state: 'visible' });
        await page.getByRole('button', { name: '应用筛选', exact: true }).waitFor();
        await page.waitForFunction(() => {
          const button = [...document.querySelectorAll('.ant-modal-footer button')]
            .find(element => element.textContent === '应用筛选');
          return button && !button.disabled;
        });
        await modal.evaluate(async element => {
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          await Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {})));
        });
        const measure = () => modal.evaluate(element => {
          const rect = selector => element.querySelector(selector).getBoundingClientRect().toJSON();
          return { content: rect('.ant-modal-content'), body: rect('.ant-modal-body'),
            footer: rect('.ant-modal-footer'), viewport: window.innerHeight };
        });
        const before = await measure();
        assert(before.content.top >= 0 && before.content.bottom <= height + 1,
          `${name} ${width}x${height}: content outside viewport ${JSON.stringify(before)}`);
        assert(before.footer.bottom <= height && before.body.height > 0,
          `${name}: actions hidden or body collapsed`);
        await modal.locator('.ant-modal-body').evaluate(element => { element.scrollTop = element.scrollHeight; });
        const after = await measure();
        assert(Math.abs(after.footer.top - before.footer.top) < 1,
          `footer moved when scrolling body: ${JSON.stringify({ before, after })}`);
        if (simulateKeyboard) {
          await modal.locator('input').first().focus();
          await page.evaluate(() => {
            window.visualViewport.height = 320;
            window.visualViewport.offsetTop = 120;
            window.visualViewport.dispatchEvent(new Event('resize'));
            window.visualViewport.dispatchEvent(new Event('scroll'));
          });
          await page.waitForFunction(() => {
            const modal = document.querySelector('.timetable-target-filter-modal .ant-modal');
            return modal.style.top === '128px';
          });
          const keyboard = await measure();
          assert(keyboard.content.top >= 120 && keyboard.content.bottom <= 440,
            `keyboard obscures modal: ${JSON.stringify(keyboard)}`);
          await page.getByRole('button', { name: '应用筛选', exact: true }).click({ trial: true });
          await page.evaluate(() => {
            window.visualViewport.height = innerHeight;
            window.visualViewport.offsetTop = 0;
            window.visualViewport.dispatchEvent(new Event('resize'));
          });
          await page.waitForFunction(() => (
            document.querySelector('.timetable-target-filter-modal .ant-modal').style.top === '8px'
          ));
        }
        if (process.env.QA_SCREENSHOT && width === 375 && height === 812 && name === '教室课表') {
          await modal.locator('.ant-modal-body').evaluate(element => { element.scrollTop = 0; });
          await page.screenshot({ path: process.env.QA_SCREENSHOT });
        }
        // Trial clicks check hit testing, not just DOM presence.
        await page.getByRole('button', { name: '清空筛选', exact: true }).click({ trial: true });
        await page.getByRole('button', { name: '应用筛选', exact: true }).click();
        await modal.waitFor({ state: 'hidden' });
        console.log(`PASS ${name} ${width}x${height}`);
      }
      assert.deepEqual(pageErrors, [], 'browser runtime errors');
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
