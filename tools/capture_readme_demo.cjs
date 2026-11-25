const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');

const frontend = path.resolve(__dirname, '../frontend');
const { chromium } = require(require.resolve('playwright', { paths: [frontend] }));
const { preview } = require(require.resolve('vite', { paths: [frontend] }));
const output = path.resolve(__dirname, '../assets/demo');
const captures = new Set(process.argv.slice(2));
const term = { code: '2026-2027-1', name: '2026–2027 学年秋季学期', current: true };
const spring = { code: '2025-2026-2', name: '2025–2026 学年春季学期' };
const autumn = { code: '2025-2026-1', name: '2025–2026 学年秋季学期' };
const courses = [
  ['DEMO101', '数据结构与算法', 4, 93], ['DEMO102', '离散数学', 3, 88],
  ['DEMO103', '计算机组成原理', 3.5, 91], ['DEMO104', '概率论与数理统计', 3, 86],
  ['DEMO105', '面向对象程序设计', 3, 95], ['DEMO106', '数据库系统原理', 3, 90],
  ['DEMO107', '线性代数', 3, 87], ['DEMO108', '学术英语与写作', 2, 92],
  ['DEMO109', '高等数学', 5, 89], ['DEMO110', '数字逻辑', 3, 85],
  ['DEMO111', '认知科学导论', 2, 94], ['DEMO112', '可视化设计基础', 2, 96],
  ['DEMO201', '操作系统', 4], ['DEMO202', '计算机网络', 3.5],
  ['DEMO203', '软件工程实践', 3], ['DEMO204', '人机交互设计', 2],
  ['DEMO205', '信息检索与推荐', 2], ['DEMO206', '数字制造入门', 2],
].map(([code, name, credit, score], index) => ({
  code, name, credit, score, term: index < 6 ? spring : index < 12 ? autumn : term,
  elective: [10, 11, 15, 16, 17].includes(index),
}));
const scores = courses.filter(course => course.score).map(course => ({
  code: course.code, name: course.name, credit: course.credit, score: String(course.score),
  score_value: course.score, gpa: (course.score - 50) / 10, term: course.term.code, term_display: course.term.name,
  course_type: course.elective ? '选修' : '必修', course_nature: course.elective ? '选修' : '必修',
  course_category: course.elective ? '通识选修' : '专业基础', exam_type: course.elective ? '考查' : '考试', is_passed: true,
}));
const planCourse = (course, selected) => ({
  course_code: course.code, course_name: course.name, credit: course.credit,
  course_nature: course.elective ? '选修' : '必修',
  status: course.score ? '已通过' : selected ? '已选课' : '未修读',
  is_passed: Boolean(course.score), is_selected: selected, is_planned: !course.score && !selected,
  term_code: course.term.code, plan_term: course.term.code, score: course.score,
});
const category = (wid, name, required, type, members, parent) => {
  const rows = members.map(index => planCourse(courses[index], index >= 12 && index < 15));
  const earned = rows.reduce((total, row) => total + (row.is_passed ? row.credit : 0), 0);
  const selected = rows.reduce((total, row) => total + (row.is_selected ? row.credit : 0), 0);
  const pathArray = parent ? [parent, name] : [name];
  return { wid, name, path: pathArray.join(' > '), path_array: pathArray, requirement_type: type,
    required_credits: required, earned_credits: earned, selected_credits: selected, remaining_credits: required - earned,
    courses: rows, children: [] };
};
const foundations = category('foundation', '学科基础必修', 36, 'required', [0, 1, 2, 3, 4, 5, 6, 8, 9], '专业教育');
const core = category('core', '专业核心必修', 14, 'required', [12, 13, 14], '专业教育');
const general = category('general', '通识教育', 16, 'elective', [10, 11, 15, 17]);
const report = {
  categories: [
    { ...category('major', '专业教育', 50, 'required', []), earned_credits: foundations.earned_credits, selected_credits: core.selected_credits,
      remaining_credits: 50 - foundations.earned_credits, children: [foundations, core] },
    general, category('language', '公共基础', 4, 'required', [7]),
    category('practice', '实践与拓展', 4, 'elective', [16]),
  ],
  credit_summary: { total_required: 74, total_passed: 36.5, total_selected: 10.5, total_remaining: 37.5, completion_rate: 49.3 },
  cache: { revision: 'demo-plan', dependency_revisions: { scores: 'demo-scores' }, is_stale: false },
};
const gradingScales = Object.fromEntries(courses.map(course => [course.code, '百分制']));
const gpaPolicy = { mode: 'from_2025', default_mode: 'from_2025', report_available: true,
  general_elective_codes: courses.filter(course => course.elective).map(course => course.code), grading_scales: gradingScales };
const outlineItems = courses.map(course => ({ course_code: course.code, course_name: course.name, credits: course.credit,
  hours: course.credit * 16, department: course.elective ? '创新创业学院' : '计算机科学与工程学院', level: '本科' }));
const outlineSections = {
  teaching: [{ title: '主要教学内容', kind: 'table', columns: ['教学单元', '学时', '主要内容'], rows: [
    { 教学单元: '线性结构', 学时: '16', 主要内容: '线性表、栈、队列和字符串' },
    { 教学单元: '树与图', 学时: '24', 主要内容: '二叉树、查找树、图的遍历与最短路径' },
    { 教学单元: '排序与检索', 学时: '24', 主要内容: '比较排序、散列表和综合实验' },
  ] }],
  assessment: [{ title: '考核组成', kind: 'table', columns: ['项目', '权重'], rows: [
    { 项目: '期末考试', 权重: '60%' }, { 项目: '实验作业', 权重: '25%' }, { 项目: '平时表现', 权重: '15%' },
  ] }],
  governance: [{ title: '历史附件', kind: 'attachments', items: [{ name: '数据结构与算法教学大纲（演示）.pdf' }] }],
};
const activities = [
  ['科技节', '校园导航算法挑战赛', '2026-11-14', '信息楼 B202', '竞赛', '二等奖', true],
  ['创意节', '未来校园交互设计工作坊', '2026-11-07', '设计工坊 201', '工作坊', '', true],
  ['科普节', '开源硬件与传感器实验', '2026-10-24', '工程训练中心', '实践活动', '', true],
  ['创业节', '从校园需求到产品原型', '2026-10-17', '创新空间 302', '路演', '', false],
  ['科技节', '校园数据可视化大赛', '2026-09-26', '信息楼 A301', '竞赛', '优秀奖', true],
  ['科普节', '科学计算开放实验室', '2026-09-19', '实验中心 106', '科普参观', '', false],
].map(([section, name, date, location, category, award, certificate_available], index) => ({
  id: `demo-activity-${index + 1}`, section, name, location, category, award, certificate_available,
  start_time: `${date}T14:00:00`, activity_time: `${date} 14:00–16:00`,
  status: '已参与', description: `完成${name}的现场签到、实践任务与活动交流。`,
}));
const scheduleColors = ['#557dcc', '#2b896e', '#8a67bd', '#cc7a41', '#348baf', '#b95674'];
const schedules = [
  [12, 1, 1, 2, '信息楼 A203'], [13, 1, 5, 6, '信息楼 B302'], [15, 1, 7, 8, '设计工坊 201'],
  [13, 2, 3, 4, '信息楼 B302'], [14, 2, 7, 8, '软件实验室 305'],
  [17, 3, 3, 4, '工程训练中心 106'], [15, 3, 3, 4, '设计工坊 202'], [16, 3, 3, 4, '信息楼 A301'],
  [14, 3, 5, 6, '软件实验室 305'], [12, 4, 1, 2, '信息楼 A203'],
  [16, 4, 5, 6, '信息楼 A301'], [13, 5, 3, 4, '网络实验室 208'], [17, 5, 7, 8, '工程训练中心 106'],
];
const isoWeekDate = (index, offset) => new Date(Date.UTC(2026, 7, 30 + index * 7 + offset)).toISOString().slice(0, 10);
const agenda = {
  revision: 1, moves: [], events: [
    { id: 'demo-agenda-review', title: '学习小组 · 算法复盘', selection_mode: 'date', dates: ['2026-11-11'],
      start_time: '16:00', end_time: '17:30', location: '图书馆研讨间 A3', note: '带上实验报告和错题笔记', important: '小组讨论' },
    { id: 'demo-agenda-lab', title: '提交网络实验报告', selection_mode: 'date', dates: ['2026-11-13'],
      start_time: '14:00', end_time: '15:30', location: '计算机实验中心', important: '截止提醒' },
  ],
};
const personal = { term_code: term.code, campuses: [{ code: '00', name: '南湖校区' }],
  weeks: Array.from({ length: 18 }, (_, index) => ({ number: index + 1, name: `第${index + 1}周`, current: index === 10,
    start_date: isoWeekDate(index, 0), end_date: isoWeekDate(index, 6) })),
  sections: [], unscheduled: [], practices: [], is_fresh: true,
  courses: schedules.map(([index, weekday, start_section, end_section, location], position) => ({
    id: `demo-schedule-${position}`, color: scheduleColors[index - 12],
    course_code: courses[index].code, course_name: courses[index].name,
    weekday, start_section, end_section, weeks: [11], location,
    campus: '南湖校区', course_nature: courses[index].elective ? '选修' : '必修',
  })),
};
const timetable = { terms: [term, spring, autumn], current: term.code, personal: [personal] };
const batch = { code: 'demo', name: '秋季学期选课方案', state: 'ended', term_code: term.code, selection_type_code: '02' };
const groups = courses.slice(12).map((course, index) => ({ group_id: course.code, course_code: course.code,
  course_name: course.name, credits: course.credit, score_scale: '百分制',
  exam_type: course.elective ? '考查' : '考试', department: '计算机学院',
  course_nature: course.elective ? '选修' : '必修',
  class_count: 1, classes: [{ class_id: `demo-class-${index}`, course_code: course.code,
    course_name: course.name, credit: course.credit, teaching_class_type: course.elective ? 'XGKC' : 'FAKC',
    teacher: ['王老师', '赵老师', '周老师'][index % 3], location: `教学楼 ${201 + index}`, capacity: 50, selected_count: 24 + index * 3,
    schedules: [{ weekday: index % 5 + 1, start_section: 3, end_section: 4, weeks: [11], location: `教学楼 ${201 + index}` }],
  }] }));
const planGroup = { group_id: 'demo-electives', name: '秋季通识选修', target_count: 1 };
const selectionPlan = groups.slice(3).map(group => ({ ...group.classes[0], plan_group_id: planGroup.group_id }));

async function capture() {
  const server = await preview({ configFile: false, root: frontend, build: { outDir: 'build' }, preview: { host: '127.0.0.1', port: 0 } });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ channel: process.env.NEU_QA_BROWSER || 'chrome' });
    await fs.mkdir(output, { recursive: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 940 }, reducedMotion: 'reduce',
      serviceWorkers: 'block', locale: 'zh-CN', timezoneId: 'Asia/Shanghai' });
    const auth = { is_logged_in: true, current_user: '20259999' };
    const responses = {
      '/api/access/status': { required: false, authenticated: true },
      '/api/client/bootstrap': { auth, runtime: { profile: 'development' }, timetable }, '/api/status': auth,
      '/api/academic-report/cache': report, '/api/gpa-policy': gpaPolicy,
      '/api/scores/cache': { scores, total_courses: scores.length, source: 'local', calculated_gpa: 4.04,
        overall_gpa: 4.02, official_major_rank: 18, gpa_policy: gpaPolicy, cache: { revision: 'demo-scores', is_stale: false } },
      '/api/course-outlines/metadata/read': { items: courses.map(course => ({ course_code: course.code,
        grading_scale: '百分制', assessment_method: course.elective ? '考查' : '考试', status: 'success', needs_sync: false })) },
      '/api/course-outlines/search-schema': { fields: [
        { key: 'KKDWDM', label: '开课单位', enabled: true, options: [{ label: '计算机科学与工程学院', value: 'cs' }] },
        { key: 'KCCCDM', label: '课程层次', enabled: true, options: [{ label: '本科', value: 'undergrad' }] },
        { key: 'KCJBDM', label: '课程级别', enabled: true, options: [{ label: '专业基础', value: 'core' }] },
      ] },
      '/api/course-outlines/search': { items: outlineItems.slice(0, 9), total: outlineItems.length, page: 1, page_size: 20 },
      '/api/course-outlines/detail/overview': { ...outlineItems[0], assessment_method: '考试', grading_scale: '百分制',
        course_nature: '必修', applicable_majors: '计算机科学与技术、软件工程', textbooks: ['数据结构（C 语言版）', '算法设计与分析'],
        introduction: '从线性表、树和图入手学习数据的组织方式，掌握查找、排序和图算法，并通过上机实验比较时间与空间开销。课程以校园路线规划和文本检索为例，训练将实际需求转换为数据模型的能力。' },
      '/api/export/festival-activities/status': { network_mode: 'follow', effective_network_mode: 'direct',
        primary_authenticated: true, service_authenticated: true, service_auth_state: 'authenticated' },
      '/api/export/festival-activities': { available: true, source: 'remote', activities, total: activities.length, warnings: [] },
      '/api/timetable/terms': timetable, '/api/timetable/bootstrap': timetable, '/api/timetable/personal': personal,
      '/api/timetable/agenda': agenda, '/api/timetable/sync': { jobs: [] },
    };
    await context.route('**/*', async route => {
      const request = route.request();
      const { origin: target, pathname } = new URL(request.url());
      if (target !== origin) return route.abort();
      if (!pathname.startsWith('/api/')) return route.continue();
      if (pathname.includes('avatar')) return route.fulfill({ status: 204 });
      let data = responses[pathname] || {};
      if (pathname.endsWith('/detail/sections')) data = { sections: outlineSections[request.postDataJSON().group] };
      if (pathname.endsWith('/jwxk/status')) data = { batches: [batch], service_authenticated: true };
      if (pathname.endsWith('/plan/read')) data = { batch, batch_code: batch.code, items: selectionPlan, groups: [planGroup], experiment_selections: {} };
      if (pathname.endsWith('/selected')) data = { selected: [], volunteered: [] };
      if (pathname.endsWith('/catalog/search')) data = { groups, total: groups.length, cache_hit: true, scope: 'ALL' };
      if (['/api/timetable/context', '/api/timetable/schedule'].includes(pathname)) data = { ...personal, mode: 'personal', campus_code: '00' };
      return route.fulfill({ json: data });
    });

    const render = async (route, filename, prepare, viewport) => {
      if (captures.size && !captures.has(filename)) return;
      const page = await context.newPage();
      if (viewport) await page.setViewportSize(viewport);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.setDefaultTimeout(12000);
      await page.clock.setFixedTime(new Date('2026-11-11T11:30:00+08:00'));
      await page.goto(`${origin}${route}`);
      await prepare(page);
      await page.evaluate(() => document.fonts.ready);
      assert.deepEqual(errors, []);
      await page.screenshot({ path: path.join(output, `${filename}.png`), animations: 'disabled' });
      await page.close();
      console.log(`Captured ${filename}`);
    };
    await render('/scores', 'scores', async page => page.getByText('数据结构与算法', { exact: true }).first().waitFor());
    await render('/academic-report', 'academic-plan', async page => {
      await page.getByText('数据结构与算法', { exact: true }).first().waitFor();
      await page.getByText('学分统计', { exact: true }).first().click();
    });
    await render('/scores', 'gpa-simulator', async page => {
      await page.getByRole('button', { name: /GPA模拟$/ }).click();
      await page.locator('.gpa-stats-row').waitFor();
      await page.locator('.gpa-table .ant-table-row').filter({ hasText: '数据结构与算法' }).first().waitFor();
      await page.getByRole('button', { name: /从计划导入$/ }).click();
      const drawer = page.locator('.gpa-import-drawer');
      for (const name of ['操作系统', '计算机网络', '软件工程实践', '人机交互设计']) {
        await drawer.getByRole('row').filter({ hasText: name }).getByRole('checkbox').check();
      }
      await drawer.getByRole('button', { name: /导\s*入$/ }).click();
      await drawer.locator('[role="dialog"]').waitFor({ state: 'hidden' });
      await page.getByText(/^模拟\s*4$/).click();
      const simulated = page.locator('.gpa-table .ant-table-row');
      for (const [index, gpa] of ['4.2', '3.9', '4.5', '4.1'].entries()) {
        const input = simulated.nth(index).locator('input').first();
        await input.fill(gpa);
        await input.blur();
      }
      await page.locator('.ant-message-notice').last().waitFor({ state: 'hidden' });
    });
    await render('/timetable?week=11', 'timetable', async page => {
      const stack = page.getByRole('group', { name: /星期三第3至4节，共3项安排/ });
      const first = stack.getByRole('button', { name: /^数字制造入门/ });
      const alternate = stack.getByRole('button', { name: /^人机交互设计/ });
      await first.waitFor();
      assert.equal(await stack.getByRole('button').count(), 3);
      assert.equal(await alternate.getAttribute('aria-expanded'), 'false');
      await alternate.focus();
      assert.equal(await alternate.getAttribute('aria-expanded'), 'true');
      await first.focus();
      await first.blur();
      assert.equal(await first.getByText('第3–4节', { exact: true }).count(), 1);
      const title = await first.locator('strong').boundingBox();
      const badge = await first.getByText('冲突', { exact: true }).boundingBox();
      assert.ok(title && badge && title.y + title.height <= badge.y);
      await page.getByRole('button', { name: /学习小组 · 算法复盘/ }).waitFor();
    });
    await render('/timetable?week=11', 'timetable-agenda-mobile', async page => {
      const wednesday = page.locator('.timetable-mobile-day-selector .ant-segmented-item').filter({ hasText: '周三' });
      await wednesday.waitFor();
      if (!await wednesday.locator('input').isChecked()) await wednesday.click();
      await wednesday.click();
      const agendaDialog = page.locator('.timetable-day-agenda-modal .ant-modal-content');
      await agendaDialog.getByText('学习小组 · 算法复盘', { exact: true }).waitFor();
      await agendaDialog.getByText('软件工程实践', { exact: true }).waitFor();
      await page.waitForFunction(() => {
        const panel = document.querySelector('.timetable-day-agenda-modal .ant-modal-content');
        const bounds = panel?.getBoundingClientRect();
        return bounds && bounds.top < innerHeight / 2 && bounds.bottom <= innerHeight + 4;
      });
    }, { width: 430, height: 1000 });
    await render('/course-outlines', 'course-outline', async page => {
      await page.getByText('数据结构与算法', { exact: true }).click();
      await page.getByText('考核组成', { exact: true }).waitFor();
    });
    await render('/export/festival-activities', 'festival-certificates', async page => {
      await page.getByText('每次使用按需读取', { exact: true }).first().click();
      await page.getByRole('button', { name: /确认并读取活动/ }).click();
      await page.getByText('校园数据可视化大赛', { exact: true }).first().waitFor();
      await page.getByRole('button', { name: /打包.*证书/ }).waitFor();
      await page.locator('.ant-message-notice').last().waitFor({ state: 'hidden' });
    });
    await render('/course-selection/demo', 'course-selection', async page => {
      await page.locator('.jwxk-course-group').first().waitFor();
      await page.locator('.jwxk-catalog-layout').evaluate(element => {
        window.scrollBy(0, element.getBoundingClientRect().top - 90);
      });
    });
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.httpServer.close(resolve));
  }
}

capture().catch(error => { console.error(error); process.exitCode = 1; });
