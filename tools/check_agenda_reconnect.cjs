const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || require.resolve('playwright', { paths: [require.resolve('../frontend/package.json')] }));

const origin = process.argv[2];
const agenda = '/api/timetable/agenda?term_code=2026-2027-1';

async function request(page, path, method = 'GET', body) {
  return page.evaluate(async ({ path, method, body }) => {
    const response = await fetch(path, {
      method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    return { status: response.status, data: await response.json() };
  }, { path, method, body });
}

async function check() {
  const browser = await chromium.launch({ channel: process.env.QA_BROWSER || 'msedge' });
  try {
    const pages = [];
    for (const username of ['student-a', 'student-a', 'student-b']) {
      const page = await browser.newPage();
      await page.goto(`${origin}/api/status`);
      const login = await request(page, '/api/login', 'POST', { username, password: 'test-only' });
      assert.equal(login.status, 200);
      pages.push(page);
    }
    const [writer, reader, other] = pages;
    await checkSharedPlan(writer, reader, other);
    const snapshot = await request(reader, agenda);
    assert.equal(snapshot.status, 200);
    assert.equal(snapshot.data.revision, 0);
    await reader.context().setOffline(true);
    await assert.rejects(request(reader, agenda), /fetch/i);
    const draft = { revision: 0, moves: [], events: [{ id: 'item', title: '合成日程', date: '2099-01-02', start_time: '09:00', end_time: '10:00' }] };
    const saved = await request(writer, agenda, 'PUT', draft);
    assert.equal(saved.status, 200);
    assert.equal(saved.data.revision, 1);
    await reader.context().setOffline(false);
    const stale = await request(reader, agenda, 'PUT', { revision: 0, moves: [], events: [] });
    assert.equal(stale.status, 409);
    const latest = await request(reader, agenda);
    assert.equal(latest.status, 200);
    assert.deepEqual(latest.data, saved.data);
    const independent = await request(other, agenda);
    assert.equal(independent.status, 200);
    assert.deepEqual(independent.data.events, []);
    assert.equal(independent.data.revision, 0);
    assert.equal((await request(writer, '/api/logout', 'POST')).status, 200);
    assert.equal((await request(writer, agenda)).status, 401);
    const separateDraft = { ...draft, events: [{ ...draft.events[0], title: '第二账号日程' }] };
    const separateSave = await request(other, agenda, 'PUT', separateDraft);
    assert.equal(separateSave.status, 200);
    const separateRead = await request(other, agenda);
    assert.equal(separateRead.status, 200);
    assert.equal(separateRead.data.events[0].title, '第二账号日程');
    assert.equal(separateRead.data.revision, 1);
    await checkAgendaPage(browser);
  } finally {
    await browser.close();
  }
}

async function checkAgendaPage(browser) {
  const term = '2099-2100-1';
  const weeks = [{ number: 1, name: '第1周', current: true, start_date: '2099-09-07', end_date: '2099-09-13' }];
  const course = { id: 'class', course_name: '测试课程', weekday: 2, weeks: [1], start_section: 1, end_section: 2, teachers: [], location: '测试教室' };
  const personal = { term_code: term, campuses: [{ code: '00', name: '南湖校区' }], weeks, sections_by_campus: {}, courses: [course], practices: [], unscheduled: [], is_fresh: true };
  const timetable = { current: term, terms: [{ code: term, name: '测试学期', current: true }], personal: [personal] };
  const auth = { is_logged_in: true, current_user: 'student-b' };
  for (const editing of [true, false]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, serviceWorkers: 'block' });
    await page.goto(`${origin}/api/status`);
    assert.equal((await request(page, '/api/login', 'POST', { username: auth.current_user, password: 'test-only' })).status, 200);
    await page.clock.setFixedTime(new Date('2099-09-08T08:00:00+08:00'));
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/timetable/agenda') return route.continue();
      const fixtures = { '/api/status': auth, '/api/access/status': { required: false, authenticated: true }, '/api/client/bootstrap': { runtime: { profile: 'development' }, auth, timetable }, '/api/timetable/terms': timetable, '/api/timetable/bootstrap': timetable, '/api/timetable/personal': personal, '/api/timetable/sync': { jobs: [] } };
      return route.fulfill({ json: fixtures[path] || {} });
    });
    await page.goto(`${origin}/timetable?week=1`);
    try {
      await page.getByRole('button', { name: '查看星期四当日日程', exact: true }).click({ timeout: 8000 });
    } catch (error) {
      console.error(await page.locator('body').innerText());
      throw error;
    }
    const modal = page.locator('.timetable-day-agenda-modal .ant-modal-content');
    if (editing) {
      await modal.getByRole('button', { name: '添加日程' }).click();
      await modal.getByLabel('标题', { exact: true }).fill('跨窗口验证');
      const response = page.waitForResponse(value => new URL(value.url()).pathname === '/api/timetable/agenda' && value.request().method() === 'PUT');
      await modal.getByRole('button', { name: '保存日程' }).click();
      assert.equal((await response).status(), 200);
    }
    await modal.getByRole('button', { name: '编辑跨窗口验证', exact: true }).waitFor();
  }
}

async function checkSharedPlan(writer, reader, other) {
  const path = '/api/gpa-simulation/file/shared.json';
  const save = (page, revision, name) => request(page, '/api/gpa-simulation/export', 'POST', {
    filename: 'shared', revision, data: { courses: [{ name, credit: 2, gpa: 3 }] },
  });
  assert.equal((await save(writer, 0, '初稿')).status, 200);
  const first = await request(reader, path);
  assert.equal(first.status, 200);
  assert.equal(first.data._document_revision, 1);
  assert.equal((await request(other, path)).status, 404);
  assert.equal((await save(other, 0, '另一账号')).status, 200);
  assert.equal((await save(writer, first.data._document_revision, '修订稿')).status, 200);
  assert.equal((await save(reader, first.data._document_revision, '过期草稿')).status, 409);
  for (const [page, owner, revision, name] of [[reader, 'student-a', 2, '修订稿'], [other, 'student-b', 1, '另一账号']]) {
    const result = await request(page, path);
    assert.equal(result.status, 200);
    assert.equal(result.data.export_info.exported_by, owner);
    assert.equal(result.data._document_revision, revision);
    assert.equal(result.data.courses[0].name, name);
  }
}

check().catch(error => { console.error(error); process.exitCode = 1; });
