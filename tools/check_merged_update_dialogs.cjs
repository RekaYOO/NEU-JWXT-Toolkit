// Browser regression for the two merged change dialogs. All APIs are intercepted.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const build = path.resolve(__dirname, '../frontend/build');
const term = { code: '2026-2027-1', name: '测试学期', current: true };
const weeks = [{ number: 2, name: '第2周', current: true, start_date: '2026-09-13', end_date: '2026-09-19' }];
const oldTimetable = {
  term_code: term.code, campuses: [{ code: '00', name: '南湖校区' }], weeks,
  sections: [], sections_by_campus: {}, unscheduled: [], practices: [], is_fresh: true,
  cache: { revision: 'before', last_checked_at: '2026-09-23T08:00:00Z' },
  courses: [{ id: 'class-1', course_name: '课程安排变更示例', weekday: 4, weeks: [2],
    start_section: 1, end_section: 2, location: '旧教室', teachers: ['测试教师'] }],
};
const newTimetable = {
  ...oldTimetable,
  cache: { revision: 'after', last_checked_at: '2026-09-24T08:00:00Z' },
  courses: [{ ...oldTimetable.courses[0], location: '新教室' }],
};
const academic = revision => ({
  categories: [], credit_summary: { total_required: revision === 'before' ? 20 : 22 },
  cache: { revision, saved_at: '2026-09-24T08:00:00Z', is_stale: false },
});
const messages = { messages: [{
  id: 'official-change-1', kind: 'reminder', title: '课程变更与培养计划学分课表通知',
  content: '教务系统通知：'.repeat(65) + '请核对具体课程与课表安排。',
  sent_at: '2026-09-24 08:00:00', read: false,
}] };
const auth = { is_logged_in: true, current_user: 'visual-fixture' };

const server = http.createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const filename = path.resolve(build, `.${pathname === '/' || !path.extname(pathname) ? '/index.html' : pathname}`);
    if (filename !== path.join(build, 'index.html') && !filename.startsWith(`${build}${path.sep}`)) {
      response.writeHead(403).end();
      return;
    }
    const type = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
      '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(filename)];
    response.setHeader('Content-Type', type || 'application/octet-stream');
    response.end(await fs.readFile(filename));
  } catch {
    response.writeHead(404).end();
  }
});

async function inspectModal(page, label, width, height, screenshots) {
  const dialog = page.locator('.ant-modal:visible').filter({ has: page.locator('.merged-update-summary') });
  await dialog.waitFor({ timeout: 15000 });
  const summary = dialog.locator('.merged-update-summary');
  await summary.getByText('教务系统未读消息（供对照）').waitFor();
  await dialog.evaluate(async element => {
    await Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {})));
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  await page.waitForTimeout(450);
  const measurements = await dialog.evaluate(element => {
    const bounds = node => node.getBoundingClientRect().toJSON();
    const content = bounds(element.querySelector('.ant-modal-content'));
    const body = bounds(element.querySelector('.ant-modal-body'));
    const actions = element.querySelector('.ant-modal-footer, .ant-modal-confirm-btns');
    const footer = bounds(actions);
    const summary = element.querySelector('.merged-update-summary');
    const buttons = [...actions.querySelectorAll('button')].map(bounds);
    return {
      viewport: { width: innerWidth, height: innerHeight },
      pageOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      content, body, footer, buttons, summary: bounds(summary),
      summaryOverflow: summary.scrollWidth > summary.clientWidth + 1,
      summaryScrollable: summary.scrollHeight > summary.clientHeight + 1,
      bodyOverflow: element.querySelector('.ant-modal-body').scrollWidth > element.querySelector('.ant-modal-body').clientWidth + 1,
    };
  });
  await page.screenshot({ path: path.join(screenshots, `${label}-${width}x${height}.png`) });
  assert(!measurements.pageOverflow && !measurements.summaryOverflow && !measurements.bodyOverflow,
    `${label} ${width}: horizontal overflow ${JSON.stringify(measurements)}`);
  if (width <= 430) {
    assert(measurements.summaryScrollable, `${label} ${width}: long notice should scroll in the dialog`);
  }
  assert(measurements.content.x >= -1
    && measurements.content.right <= width + 1
    && measurements.content.y >= -1
    && measurements.content.bottom <= height + 1,
  `${label} ${width}: dialog outside viewport ${JSON.stringify(measurements)}`);
  assert(measurements.summary.bottom <= measurements.footer.y + 1,
    `${label} ${width}: footer obscured ${JSON.stringify(measurements)}`);
  assert(measurements.buttons.length >= 2 && measurements.buttons.every(button => (
    button.width > 50 && button.x >= measurements.content.x - 1
    && button.right <= measurements.content.right + 1 && button.bottom <= height + 1
  )), `${label} ${width}: actions inaccessible ${JSON.stringify(measurements)}`);
  if (measurements.summaryScrollable) {
    await summary.evaluate(element => { element.scrollTop = element.scrollHeight; });
    assert(await summary.evaluate(element => element.scrollTop > 0), `${label} ${width}: notice cannot scroll`);
  }
  assert(await dialog.locator('.ant-modal-footer button, .ant-modal-confirm-btns button').last().isVisible(),
    `${label} ${width}: action hidden after scrolling`);
}

(async () => {
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-merged-updates-'));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
    const viewports = [[320, 800], [375, 812], [430, 932], [768, 900], [1440, 900]];
    for (const [width, height] of viewports) {
      for (const label of ['timetable', 'academic']) {
        const page = await browser.newPage({ viewport: { width, height }, serviceWorkers: 'block' });
        const pageErrors = [];
        let updated = false;
        page.on('pageerror', error => pageErrors.push(error.message));
        await page.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.origin !== origin) return route.abort();
          if (!url.pathname.startsWith('/api/')) return route.continue();
          let data = {};
          if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
          else if (url.pathname === '/api/client/bootstrap') data = {
            runtime: { profile: 'development' }, auth,
            timetable: { terms: [term], current: term.code, personal: [oldTimetable] },
          };
          else if (url.pathname === '/api/status') data = auth;
          else if (url.pathname === '/api/health') data = { profile: 'development' };
          else if (url.pathname === '/api/timetable/bootstrap') data = {
            terms: [term], current: term.code, personal: [updated ? newTimetable : oldTimetable],
          };
          else if (url.pathname === '/api/timetable/terms') data = { terms: [term], current: term.code };
          else if (url.pathname === '/api/timetable/personal') data = updated ? newTimetable : oldTimetable;
          else if (url.pathname === '/api/timetable/sync') data = { jobs: [] };
          else if (url.pathname === '/api/timetable/agenda') data = { revision: 0, events: [], moves: [] };
          else if (url.pathname === '/api/academic-report/cache') data = academic(updated ? 'after' : 'before');
          else if (url.pathname === '/api/system-messages/cache') data = messages;
          else if (url.pathname === '/api/user/avatar') return route.fulfill({ status: 204 });
          else if (url.pathname.includes('/cache/refresh')) data = { status: 'fresh' };
          return route.fulfill({ json: data });
        });
        try {
          await page.goto(`${origin}/${label === 'timetable' ? 'timetable' : 'academic-report'}`);
          if (label === 'academic') {
            await page.locator('.academic-report-page').waitFor({ timeout: 15000 });
            await page.waitForFunction(() => document.querySelector('.credit-summary-float')?.textContent.includes('0/20'));
            updated = true;
            await page.evaluate(() => window.dispatchEvent(new Event('focus')));
          } else {
            await page.locator('.timetable-page').waitFor();
            await page.waitForFunction(() => document.querySelector('.timetable-page')?.textContent.includes('课程安排变更示例'));
            updated = true;
            await page.evaluate(() => window.dispatchEvent(new CustomEvent('neu-cache-event', {
              detail: { resource: 'personal-timetable' },
            })));
          }
          await inspectModal(page, label, width, height, screenshots);
          assert.deepEqual(pageErrors, [], `${label} ${width}: browser exceptions`);
          console.log(`PASS ${label} ${width}x${height}`);
        } catch (error) {
          console.error({ label, width, pageErrors, text: (await page.locator('body').innerText()).slice(0, 1200) });
          await page.screenshot({ path: path.join(screenshots, `${label}-${width}x${height}-failed.png`) });
          throw error;
        } finally {
          await page.close();
        }
      }
    }
    console.log(`Screenshots: ${screenshots}`);
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
