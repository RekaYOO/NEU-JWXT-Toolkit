import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import AcademicReportPage, {
  calcElectiveRemainingCredits,
  calcRequiredRemainingCredits,
} from './AcademicReportPage';
import { useCachedResource } from '../resources/ResourceStore';
import {
  cancelCourseOutlineMetadataSync,
  getCourseOutlinePlanMetadata,
} from '../services/api';

// This test covers the pure credit aggregation rule. Keep the page's remote
// course-outline integration outside this unit-test boundary so Jest never
// needs to load Axios' ESM entrypoint in the legacy react-scripts runtime.
jest.mock('../services/api', () => ({
  cancelCourseOutlineMetadataSync: jest.fn(),
  getCourseOutlineMetadataSyncStatus: jest.fn(),
  getCourseOutlinePlanMetadata: jest.fn(),
  startCourseOutlineMetadataSync: jest.fn(),
}));

jest.mock('../resources/ResourceStore', () => ({
  useCachedResource: jest.fn()
}));

const electiveNode = (overrides = {}) => ({
  wid: overrides.name || 'node',
  name: '选修类别',
  path: '选修类别',
  path_array: ['选修类别'],
  requirement_type: 'elective',
  required_credits: 0,
  earned_credits: 0,
  remaining_credits: 0,
  is_completed: true,
  children: [],
  ...overrides
});

test('双重约束按子类最低差额和父类总量差额自底向上去重', () => {
  const parent = electiveNode({
    name: '通识选修类',
    required_credits: 10,
    earned_credits: 6,
    remaining_credits: 4,
    aggregate_remaining_credits: 4,
    requires_child_minimums_and_total: true,
    is_completed: false,
    children: [
      electiveNode({
        name: '带内部规则的子类',
        required_credits: 4,
        earned_credits: 2,
        remaining_credits: 2,
        is_completed: false,
        children: [
          electiveNode({
            name: '内部一',
            required_credits: 1,
            earned_credits: 1,
            remaining_credits: 0
          }),
          electiveNode({
            name: '内部二',
            required_credits: 1,
            earned_credits: 1,
            remaining_credits: 0
          })
        ]
      }),
      electiveNode({
        name: '已达标子类',
        required_credits: 4,
        earned_credits: 4,
        remaining_credits: 0
      })
    ]
  });

  // 子类自身缺 2，父类总量还缺 4；最终至少再修 4，而不是漏算为 2
  // 或重复累加为 6。
  expect(calcElectiveRemainingCredits([parent])).toBe(4);
});

test('已选课程占满学分后不再计入选课缺口', () => {
  const selectedButNotPassed = {
    wid: 'required-selected',
    name: '必修',
    path: '学科基础类 > 必修',
    path_array: ['学科基础类', '必修'],
    requirement_type: 'required',
    required_credits: 4.5,
    passed_credits: 0,
    selected_credits: 4.5,
    earned_credits: 4.5,
    remaining_credits: 0,
    is_completed: false,
    courses: [
      { credit: 2, is_selected: true, is_passed: false },
      { credit: 2.5, is_selected: true, is_passed: false },
    ],
    children: [],
  };

  expect(calcRequiredRemainingCredits([selectedButNotPassed])).toBe(0);
});

test('存在选修学分缺口时培养计划页面可以正常渲染', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia = window.matchMedia || (() => ({
    matches: false,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
  }));
  global.ResizeObserver = global.ResizeObserver || class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  cancelCourseOutlineMetadataSync.mockResolvedValue({});
  getCourseOutlinePlanMetadata.mockResolvedValue({ items: [] });
  useCachedResource.mockReturnValue({
    data: {
      categories: [electiveNode({
        wid: 'humanities-elective',
        name: '选修',
        path: '人文社会科学类 > 选修',
        path_array: ['人文社会科学类', '选修'],
        required_credits: 2,
        remaining_credits: 2,
        is_completed: false,
      })],
      credit_summary: {},
      cache: { revision: 'test', saved_at: null, is_stale: false },
    },
    error: null,
    syncError: null,
    updateAvailable: false,
    availableRevision: '',
    availableData: null,
    applyAvailable: jest.fn(),
    applyData: jest.fn(),
    refresh: jest.fn(),
    reloadAndApply: jest.fn(),
  });

  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AcademicReportPage />
      </MemoryRouter>,
    );
    await new Promise(resolve => setTimeout(resolve, 0));
  });

  expect(container.textContent).toContain('选修课还差');
  expect(container.textContent).toContain('还差 2 学分');

  await act(async () => root.unmount());
  container.remove();
  global.IS_REACT_ACT_ENVIRONMENT = false;
});
