import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import OfficialWithdrawalWorkspacePage from './OfficialWithdrawalWorkspacePage';
import { getOfficialWithdrawalCourses } from '../services/api';

jest.mock('../services/api', () => ({
  getOfficialWithdrawalCourses: jest.fn(),
  deselectOfficialCourse: jest.fn(),
}));

jest.mock('../pages/TimetablePage', () => ({ overlayCourses }) => (
  <div data-testid="withdrawal-timetable" data-preview-count={overlayCourses.length}>课表</div>
));
jest.mock('../utils/academicReport', () => ({
  ...jest.requireActual('../utils/academicReport'),
  collectAcademicPlanDeficits: () => [{
    wid: 'required', name: '专业必修', requirement_type: 'required',
    required_credits: 4, remaining_credits: 2, unfinished_courses: [],
  }],
}));
jest.mock('../resources/ResourceStore', () => ({
  useCachedResource: jest.fn(() => ({
    data: {
      categories: [{
        wid: 'required', name: '专业必修', path: '专业必修', required_credits: 4,
        remaining_credits: 2, missing_course_count: 1, children: [], courses: [],
      }],
    },
    loading: false,
    error: null,
    syncError: null,
  })),
}));

const course = (wid, canWithdraw) => ({
  wid,
  course_name: '基础工业工程',
  course_code: 'A1441000070',
  course_serial: wid,
  course_nature: '选修',
  course_category: '通识选修',
  credits: 2,
  teacher: '测试教师',
  schedule: '第 2 周 · 星期一 · 第 1-2 节',
  department: '工业工程系',
  class_start_at: '2026-09-01 08:00:00',
  withdrawal_end_at: '2026-09-30 23:59:59',
  can_withdraw: canWithdraw,
  unavailable_reason: canWithdraw ? '' : '非自选课程不允许退课',
  penalty_label: canWithdraw ? '阶段一' : '',
  penalty_weight: canWithdraw ? 2 : null,
  weight: 20,
  schedules: [{
    meeting_id: wid, course_name: '基础工业工程', weeks: [2],
    weekday: 1, start_section: 1, end_section: 2,
  }],
});

describe('独立退课工作台', () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = jest.fn(() => ({
      matches: false, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    }));
    getOfficialWithdrawalCourses.mockResolvedValue({
      term_code: '2099-2100-1', term_name: '测试学期',
      courses: [course('1', true), {
        ...course('2', false), course_code: 'EXPIRED', course_name: '过期课程',
        unavailable_reason: '已超过允许退课时间', penalty_weight: 10,
      }],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  const renderPage = async () => {
    await act(async () => {
      root.render(<MemoryRouter><OfficialWithdrawalWorkspacePage /></MemoryRouter>);
      await Promise.resolve();
    });
  };
  const click = async element => {
    expect(element).not.toBeNull();
    await act(async () => element.dispatchEvent(new MouseEvent('click', { bubbles: true })));
  };
  const button = (container, label) => [...container.querySelectorAll('button')]
    .find(item => item.textContent.includes(label));

  test('单教学班直接展示状态、返还估算和操作，不展示无意义的教学班计数', async () => {
    await renderPage();
    expect(container.textContent).toContain('退课工作台');
    expect(container.textContent).toContain('培养计划缺口');
    expect(container.querySelector('[data-testid="withdrawal-timetable"]')).not.toBeNull();
    expect(container.querySelectorAll('.jwxk-inline-class')).toHaveLength(2);
    expect(container.querySelectorAll('.jwxk-course-group__stats')).toHaveLength(0);
    expect(container.textContent).not.toContain('教学班 1 个');
    expect(container.textContent).toContain('预计返还 18 权重');
    expect(container.textContent).toContain('2周 · 周一 · 1-2节');
    const row = container.querySelector('.jwxk-inline-class');
    expect(row.querySelector('.jwxk-inline-class__summary').textContent).toBe('测试教师2周 · 周一 · 1-2节');
    expect(row.querySelector('.jwxk-inline-class__summary .anticon')).toBeNull();
    expect(row.querySelector('.official-withdrawal-workspace__class-controls .jwxk-inline-class__states')).not.toBeNull();
    expect(row.querySelector('.official-withdrawal-workspace__class-controls .jwxk-inline-class__actions')).not.toBeNull();
    expect(container.textContent).not.toContain('退课截止');
    await click(button(container, '查看详情'));
    expect(document.body.textContent).toContain('退课时间');
  });

  test('多教学班仍可展开比较', async () => {
    getOfficialWithdrawalCourses.mockResolvedValue({
      term_code: '2099-2100-1',
      courses: [course('1', true), { ...course('2', false), teacher: '第二位教师' }],
    });
    await renderPage();
    expect(container.querySelectorAll('.jwxk-inline-class')).toHaveLength(0);
    expect(container.textContent).toContain('1 个可退 · 1 个暂不可退');
    await click(button(container, '比较教学班'));
    expect(container.querySelectorAll('.jwxk-inline-class')).toHaveLength(2);
  });

  test('预览聚焦课表，课表顶部取消和课程按钮取消均返回原课程', async () => {
    const scrollTargets = [];
    const previousScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (options) {
      scrollTargets.push({ target: this, options });
    };
    try {
      await renderPage();
      await click(button(container, '在课表中预览'));
      expect(scrollTargets.at(-1).target).toBe(container.querySelector('.jwxk-live-schedule'));
      expect(scrollTargets.at(-1).options).toEqual({ behavior: 'smooth', block: 'start' });
      expect(container.querySelector('[data-testid="withdrawal-timetable"]').dataset.previewCount).toBe('1');
      await click(container.querySelector('.jwxk-live-schedule__head button'));
      expect(scrollTargets.at(-1).target.closest('.jwxk-course-group').textContent).toContain('基础工业工程');
      expect(scrollTargets.at(-1).options).toEqual({ behavior: 'smooth', block: 'center' });
      expect(container.querySelector('[data-testid="withdrawal-timetable"]').dataset.previewCount).toBe('0');
      await click(button(container, '在课表中预览'));
      await click(button(container, '取消课表预览'));
      expect(scrollTargets.at(-1).options.block).toBe('center');
      expect(container.querySelector('[data-testid="withdrawal-timetable"]').dataset.previewCount).toBe('0');
    } finally {
      Element.prototype.scrollIntoView = previousScroll;
    }
  });

  test('多教学班收起后取消预览仍返回所属课程组', async () => {
    getOfficialWithdrawalCourses.mockResolvedValue({
      term_code: '2099-2100-1',
      courses: [course('1', true), course('2', false)],
    });
    const targets = [];
    const previousScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function () { targets.push(this); };
    try {
      await renderPage();
      await click(button(container, '比较教学班'));
      await click(button(container, '在课表中预览'));
      await click(button(container, '收起教学班'));
      await click(container.querySelector('.jwxk-live-schedule__head button'));
      expect(targets.at(-1).classList.contains('jwxk-course-group')).toBe(true);
    } finally {
      Element.prototype.scrollIntoView = previousScroll;
    }
  });

  test('已过退课时间的课程只显示不可退原因，不展示旧权重返还', async () => {
    await renderPage();
    const expired = [...container.querySelectorAll('.jwxk-course-group')]
      .find(item => item.textContent.includes('过期课程'));
    expect(expired.textContent).toContain('已超过允许退课时间');
    expect(expired.querySelector('.jwxk-inline-class__summary .official-withdrawal-workspace__reason').textContent)
      .toBe('已超过允许退课时间');
    expect(expired.querySelector('.official-withdrawal-workspace__class-controls').textContent)
      .not.toContain('已超过允许退课时间');
    expect(expired.textContent).not.toContain('预计返还');
    expect(expired.textContent).not.toContain('预计扣');
    expect(expired.querySelector('.jwxk-inline-class__actions .ant-btn-dangerous').disabled).toBe(true);
  });

  test('清除筛选同时取消培养计划缺口选中状态', async () => {
    await renderPage();
    const gap = container.querySelector('.jwxk-plan-gap');
    expect(gap).not.toBeNull();
    await click(gap);
    expect(gap.classList.contains('is-active')).toBe(true);
    await click(button(container, '清除筛选'));
    expect(gap.classList.contains('is-active')).toBe(false);
    expect(container.querySelector('.jwxk-active-filters')).toBeNull();
  });
});
