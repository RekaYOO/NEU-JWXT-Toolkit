// Production-build browser regression with anonymous, fully intercepted APIs.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const build = path.resolve(__dirname, '../frontend/build');
const term = { code: '2026-2027-1', name: '测试学期', current: true };
const weeks = Array.from({ length: 5 }, (_, index) => ({
  number: index + 1, name: `第${index + 1}周`, current: index === 0,
}));
const context = {
  campuses: [{ code: '00', name: '南湖校区' }], weeks,
  sections: Array.from({ length: 12 }, (_, index) => ({ number: index + 1, name: `第${index + 1}节` })),
};
const courses = week => Array.from({ length: 42 }, (_, index) => ({
  id: `week-${week}-course-${index}`, course_name: `第${week}周课程${index + 1}`,
  weekday: Math.floor(index / 6) + 1, start_section: (index % 6) * 2 + 1,
  end_section: (index % 6) * 2 + 2, weeks: [week],
  teachers: ['测试教师'], classes: ['测试班级'], location: '测试教学楼 101 教室',
}));
const personal = {
  term_code: term.code, ...context, sections_by_campus: { '00': context.sections },
  courses: courses(1), unscheduled: [], practices: [], is_fresh: true, source: 'local',
};
const auth = { is_logged_in: true, current_user: 'query-fixture' };

(async () => {
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-query-qa-'));
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
    const viewports = process.env.QA_VIEWPORTS ? JSON.parse(process.env.QA_VIEWPORTS)
      : [[320, 800], [375, 812], [430, 932], [768, 900], [1440, 900]];
    for (const [width, height] of viewports) {
      for (const compact of width < 992 ? [false, true] : [false]) {
        for (const label of process.env.QA_FOCUS_ONLY === '1' ? ['我的课表']
          : process.env.QA_VIEW_ONLY === '1' ? ['我的课表', '教室课表', '教师课表', '班级课表']
          : ['教室课表', '教师课表', '班级课表']) {
          const page = await browser.newPage({ viewport: { width, height }, serviceWorkers: 'block' });
          const errors = [];
          let releaseSchedule = null;
          let delayNextSchedule = false;
          let queryContext = context;
          let delayNextContext = false;
          let releaseContext = null;
          let scheduleRequests = 0;
          page.on('pageerror', error => errors.push(error.message));
          await page.addInitScript(({ compact }) => {
            history.replaceState(null, '', '/timetable');
            localStorage.setItem('neu_toolbox:mobileCompactWeekView', JSON.stringify(compact));
            window.__timetableFocusCalls = [];
            const original = Element.prototype.scrollIntoView;
            Element.prototype.scrollIntoView = function (...args) {
              if (this.closest('.timetable-page')) window.__timetableFocusCalls.push(this.className);
              return original.apply(this, args);
            };
          }, { compact });
          await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== origin) return route.abort();
            if (!url.pathname.startsWith('/api/')) return route.continue();
            let data = {};
            if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
            else if (url.pathname === '/api/client/bootstrap') data = {
              runtime: { profile: 'development' }, auth,
              timetable: { terms: [term], current: term.code, personal: [personal] },
            };
            else if (url.pathname === '/api/status') data = auth;
            else if (url.pathname === '/api/timetable/bootstrap' || url.pathname === '/api/timetable/terms') {
              data = { terms: [term], current: term.code, personal: [personal] };
            } else if (url.pathname === '/api/timetable/personal') data = personal;
            else if (url.pathname === '/api/timetable/context') {
              if (delayNextContext) {
                delayNextContext = false;
                await new Promise(resolve => { releaseContext = resolve; });
              }
              data = queryContext;
            }
            else if (url.pathname === '/api/timetable/targets/search') data = {
              items: [{ id: 'query-fixture', name: '测试查询对象', details: { campus: '南湖校区' } }],
              page: 1, total: 1,
            };
            else if (url.pathname === '/api/user/avatar') return route.fulfill({ status: 204 });
            else if (url.pathname === '/api/timetable/schedule') {
              scheduleRequests += 1;
              const request = route.request().postDataJSON();
              if (delayNextSchedule) {
                delayNextSchedule = false;
                await new Promise(resolve => { releaseSchedule = resolve; });
              }
              data = { ...request, courses: request.week == null ? [...courses(3), ...courses(4)]
                : [3, 4].includes(request.week) ? courses(request.week) : [],
                unscheduled: [], practices: [] };
            }
            return route.fulfill({ json: data });
          });
          const openQuery = async () => {
            await page.locator('.timetable-mode-tabs').waitFor();
            const tab = page.getByRole('tab', { name: label, exact: true });
            if (await tab.isVisible()) await tab.click();
            else {
              await page.locator('.timetable-mode-tabs .ant-tabs-nav-more').click();
              await page.getByRole('menuitem', { name: label, exact: true }).click();
            }
            await page.locator('.timetable-target-result-card').first().click();
          };
          await page.goto(`${origin}/`);
          if (process.env.QA_FOCUS_ONLY === '1') {
            await page.locator('.timetable-desktop').first().waitFor({ state: 'attached' });
            await page.waitForFunction(() => document.querySelector('.timetable-page')?.textContent.includes('第1周课程'));
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            const focus = await page.evaluate(() => ({
              calls: window.__timetableFocusCalls,
              top: window.scrollY,
              padding: document.querySelector('.timetable-page').style.paddingBottom,
              overflow: document.documentElement.scrollWidth > innerWidth + 1,
            }));
            if (width >= 992) {
              assert.deepEqual(focus.calls, [], 'desktop must never use mobile auto-focus, including cached startup');
              assert.equal(focus.top, 0);
              assert.equal(focus.padding, '');
            } else {
              assert.ok(focus.calls.length > 0, 'small screens must retain initial focus');
              assert.ok(focus.top > 0, 'small-screen selector must still be scrolled into view');
            }
            assert.equal(focus.overflow, false);
            assert.deepEqual(errors, []);
            await page.screenshot({
              path: path.join(screenshots, `${width}-${compact ? 'compact' : 'daily'}-initial-focus.png`),
            });
            console.log(`PASS initial focus ${width}x${height} ${compact ? 'compact' : 'default'}: calls=${focus.calls.length}, scrollY=${focus.top}`);
            await page.close();
            continue;
          }
          if (label !== '我的课表') await openQuery();
          const empty = page.getByText('当前条件下暂无课程安排', { exact: true });
          if (label !== '我的课表') await empty.waitFor();
          const selectWeek = async (number, expectRequest = true) => {
            const completed = expectRequest && !delayNextSchedule ? page.waitForResponse(response => (
              response.url().endsWith('/api/timetable/schedule')
              && response.request().postDataJSON().week === number
            )) : null;
            if (width < 992) {
              // DOM click avoids Playwright scrolling the document to reveal a
              // horizontally offscreen week before measuring the app's scroll.
              await page.locator(`[data-week="${number}"]`).evaluate(node => node.click());
            } else {
              await page.locator('.timetable-desktop-controls label')
                .filter({ hasText: '教学周' }).locator('.ant-select').click();
              await page.locator('.ant-select-item-option-content').filter({ hasText: `第${number}周` }).click();
            }
            if (completed) {
              await completed;
              await page.locator('.timetable-query-stale').waitFor({ state: 'detached' });
              await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            }
          };
          if (process.env.QA_VIEW_ONLY === '1') {
            const query = label !== '我的课表';
            if (query) await selectWeek(3);
            await page.locator('.timetable-desktop').first().waitFor({ state: 'attached' });
            const requestsBeforeFilter = scheduleRequests;
            const coursesBeforeFilter = await page.locator('.timetable-course-block').count();
            if (width < 992) {
              await page.locator('.timetable-context-copy').click();
              const drawer = page.locator('.ant-drawer-open');
              await drawer.evaluate(async element => {
                await Promise.all(element.getAnimations({ subtree: true })
                  .map(animation => animation.finished.catch(() => {})));
              });
              const timeFilter = page.locator('.mobile-sheet .timetable-time-filter-group');
              await timeFilter.evaluate(element => element.scrollIntoView({ block: 'center' }));
              await timeFilter.getByText('不上早八', { exact: true }).click();
              await timeFilter.getByText('不上晚十', { exact: true }).click();
              await page.locator('.ant-drawer-open .mobile-sheet__footer button').last().click();
              await page.waitForFunction(() => {
                const text = document.querySelector('.timetable-context-copy')?.textContent || '';
                return text.includes('不上早八') && text.includes('不上晚十');
              });
            } else {
              const timeFilter = page.locator('.timetable-desktop-controls .timetable-time-filter-group');
              await timeFilter.getByText('不上早八', { exact: true }).click();
              await timeFilter.getByText('不上晚十', { exact: true }).click();
            }
            await page.waitForFunction(before => (
              document.querySelectorAll('.timetable-course-block').length < before
            ), coursesBeforeFilter);
            assert.equal(scheduleRequests, requestsBeforeFilter, `${label}: local time filter made a schedule request`);
            assert.equal(await page.getByText(
              '当前课表中的课程均已被“不上时间”筛选排除', { exact: true },
            ).count(), 0, `${label}: fixture should retain middle-day courses`);
            if (width < 992) await page.locator('.timetable-mobile-summary-trigger').click();
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            await page.evaluate(() => {
              window.scrollTo(0, 180);
              window.__grid = document.querySelector('.timetable-desktop');
              window.__summary = document.querySelector('.timetable-mobile-summary');
              window.__removedGrid = false;
              new MutationObserver(records => {
                for (const record of records) for (const node of record.removedNodes) {
                  if (node === window.__grid || node.contains?.(window.__grid)) window.__removedGrid = true;
                }
              }).observe(document.querySelector('.timetable-query-results'), { childList: true, subtree: true });
            });
            const rangeControls = page.locator(width < 992
              ? '.timetable-mobile-summary-view' : '.timetable-desktop-controls');
            for (const [range, termView] of width < 992
              ? [['学期课表', true], ['周课表', false]] : [['全学期', true], ['按周', false]]) {
              const beforeTop = await page.evaluate(() => scrollY);
              if (query) delayNextSchedule = true;
              await rangeControls.getByText(range, { exact: true }).evaluate(node => node.click());
              if (query) {
                await page.locator('.timetable-query-transition').waitFor();
                assert.equal(await page.locator('.timetable-query-stale').evaluate(node => getComputedStyle(node).visibility), 'visible');
                assert.equal(await page.locator('.timetable-query-results .ant-skeleton').count(), 0);
                assert.equal(await page.locator('.timetable-mobile').evaluate(node => node.classList.contains('is-term-view')), !termView);
                if (label === '教室课表' && termView) await page.screenshot({
                  path: path.join(screenshots, `${width}-${compact ? 'compact' : 'daily'}-view-pending.png`),
                });
                assert(releaseSchedule);
                releaseSchedule();
                releaseSchedule = null;
                await page.locator('.timetable-query-stale').waitFor({ state: 'detached' });
              }
              await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
              const state = await page.evaluate(() => ({
                same: window.__grid === document.querySelector('.timetable-desktop'),
                removed: window.__removedGrid, top: scrollY,
                summarySame: window.__summary === document.querySelector('.timetable-mobile-summary'),
                expanded: window.__summary?.classList.contains('expanded'),
                overflow: document.documentElement.scrollWidth > innerWidth + 1,
              }));
              assert(state.same && !state.removed, `${label}: range switch remounted grid`);
              assert(Math.abs(state.top - beforeTop) < 2, `${label} ${width} ${range}: scroll jumped ${beforeTop} -> ${state.top}`);
              assert.equal(state.overflow, false);
              if (width < 992) assert(state.summarySame && state.expanded);
              assert.equal(await page.locator('.timetable-mobile').evaluate(node => node.classList.contains('is-term-view')), termView);
              if (label === '教室课表') await page.screenshot({
                path: path.join(screenshots, `${width}-${compact ? 'compact' : 'daily'}-${termView ? 'term' : 'week'}.png`),
              });
            }
            assert.deepEqual(errors, []);
            console.log(`PASS views ${label} ${width}x${height} ${compact ? 'compact' : 'daily'}: retained grid, range, scroll, summary`);
            await page.close();
            continue;
          }
          await selectWeek(2);
          await page.waitForFunction(() => !document.querySelector('[aria-busy="true"].timetable-query-results'));
          await empty.waitFor();
          await page.evaluate(() => window.dispatchEvent(new CustomEvent('neu-cache-event', {
            detail: { resource: 'personal-timetable' },
          })));
          assert.equal(await page.locator('.timetable-query-pending').count(), 0);

          await selectWeek(3);
          await page.locator('.timetable-query-stale').waitFor({ state: 'detached' });
          if (width < 992) {
            await page.locator('.timetable-mobile-summary-trigger').click();
            await page.evaluate(() => {
              window.scrollTo(0, 180);
              window.__summary = document.querySelector('.timetable-mobile-summary');
              window.__grid = document.querySelector('.timetable-query-results .timetable-desktop');
            });
          }
          const before = await page.evaluate(() => ({
            top: scrollY, height: document.querySelector('.timetable-page').getBoundingClientRect().height,
          }));
          delayNextSchedule = true;
          await selectWeek(4);
          await page.locator('.timetable-query-transition').waitFor();
          await page.getByText(`正在从教务系统读取第 4 周${label}，返回后会自动显示。`, { exact: true }).waitFor();
          assert.equal(await page.locator('.timetable-query-transition .ant-skeleton').count(), 0,
            `${label}: retained results must not be covered by a replacement skeleton`);
          assert.equal(await page.locator('.timetable-query-stale').evaluate(node => getComputedStyle(node).visibility), 'visible');
          const during = await page.evaluate(() => ({
            top: scrollY, height: document.querySelector('.timetable-page').getBoundingClientRect().height,
            summarySame: window.__summary === document.querySelector('.timetable-mobile-summary'),
            summaryExpanded: window.__summary?.classList.contains('expanded'),
            gridConnected: window.__grid?.isConnected,
          }));
          assert(Math.abs(during.height - before.height) < 2,
            `${label}: height changed during query ${JSON.stringify({ before, during })}`);
          if (width < 992) {
            assert(Math.abs(during.top - before.top) < 2, `${label}: scroll changed while pending`);
            assert(during.summarySame && during.summaryExpanded && during.gridConnected,
              `${label}: summary or grid unmounted`);
          }
          assert(releaseSchedule, 'delayed schedule request was not dispatched');
          releaseSchedule();
          releaseSchedule = null;
          await page.locator('.timetable-query-stale').waitFor({ state: 'detached' });
          if (width < 992) {
            const after = await page.evaluate(() => ({
              top: scrollY, same: window.__summary === document.querySelector('.timetable-mobile-summary'),
              expanded: window.__summary.classList.contains('expanded'),
            }));
            assert(Math.abs(after.top - before.top) < 2 && after.same && after.expanded,
              `${label}: view jumped on successful response`);
          }
          await selectWeek(5);
          await page.locator('.timetable-query-stale').waitFor({ state: 'detached' });
          await empty.waitFor();
          if (width < 992) {
            const afterEmpty = await page.evaluate(() => scrollY);
            assert(Math.abs(afterEmpty - before.top) < 2, `${label}: empty week clamped scroll position`);
            await page.locator('.timetable-mobile-summary-trigger').evaluate(node => node.click());
            await page.evaluate(() => window.scrollTo(0, 0));
            await page.waitForFunction(() => !document.querySelector('.timetable-page').style.minHeight);
          } else {
            await page.keyboard.press('Escape');
          }
          assert.deepEqual(errors, []);
          const bounds = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth }));
          assert(bounds.scroll <= bounds.width + 1, `${label}: horizontal page overflow`);
          if (label === '教室课表') {
            await page.screenshot({ path: path.join(screenshots, `${width}-${compact ? 'compact' : 'daily'}.png`), fullPage: true });
          }
          console.log(`PASS ${label} ${width}x${height} ${compact ? 'compact' : 'default'}: empty, pending, scroll, summary`);

          queryContext = {
            ...context, campuses: [],
            weeks: weeks.map(item => ({ ...item, current: item.number === 2 })),
          };
          delayNextContext = true;
          await page.goto(`${origin}/`);
          await openQuery();
          await page.getByText(`正在从教务系统读取${label}，返回后会自动显示。`, { exact: true }).waitFor();
          assert.equal(await page.getByText('该学期暂无课程安排', { exact: true }).count(), 0);
          assert(releaseContext, 'delayed context request was not dispatched');
          releaseContext();
          releaseContext = null;
          const noCampus = page.getByText('该学期暂无课程安排', { exact: true });
          const requestCount = scheduleRequests;
          const checkNoCampus = async () => {
            await noCampus.waitFor();
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            assert.equal(await page.locator('.timetable-query-pending').count(), 0);
            assert.equal(await page.locator('.timetable-query-results[aria-busy="true"]').count(), 0);
            assert.equal(await page.locator('.timetable-error').count(), 0);
            assert.equal(scheduleRequests, requestCount, 'missing campus must not dispatch schedule requests');
          };
          await checkNoCampus();
          if (width < 992) {
            assert.equal(await page.locator('[data-week="2"]').getAttribute('aria-selected'), 'true');
          } else {
            assert.match(await page.locator('.timetable-desktop-controls label')
              .filter({ hasText: '教学周' }).innerText(), /第2周/);
          }
          await selectWeek(3, false);
          await checkNoCampus();
          await selectWeek(2, false);
          await checkNoCampus();
          if (width < 992) await page.locator('.timetable-mobile-summary-trigger').click();
          const rangeControls = page.locator(width < 992
            ? '.timetable-mobile-summary-view' : '.timetable-desktop-controls');
          for (const range of width < 992 ? ['学期课表', '周课表'] : ['全学期', '按周']) {
            await rangeControls.getByText(range, { exact: true }).click();
            await checkNoCampus();
          }
          if (width < 992) await page.locator('.timetable-mobile-summary-trigger').click();
          const refreshContext = async () => {
            const button = width < 992
              ? page.getByRole('button', { name: '刷新课表', exact: true })
              : page.locator('.timetable-desktop-controls button').filter({ hasText: '刷新' });
            await Promise.all([
              page.waitForResponse(response => response.url().endsWith('/api/timetable/context')),
              button.click(),
            ]);
          };
          await refreshContext();
          await checkNoCampus();
          if (label === '教室课表') {
            await page.screenshot({ path: path.join(screenshots, `${width}-${compact ? 'compact' : 'daily'}-no-campus.png`), fullPage: true });
          }
          queryContext = { ...queryContext, campuses: context.campuses };
          await refreshContext();
          await empty.waitFor();
          assert.equal(scheduleRequests, requestCount + 1);
          assert.equal(await page.locator('.timetable-error').count(), 0);
          assert.deepEqual(errors, []);
          console.log(`PASS ${label} ${width}x${height} ${compact ? 'compact' : 'default'}: loading, empty campus, weeks 2-3-2, range, refresh`);
          await page.close();
        }
      }
    }
    console.log(`Screenshots: ${screenshots}`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
