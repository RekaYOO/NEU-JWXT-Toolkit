import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import CourseSelectionWorkspacePage, {
  clearExperimentSelectionsForClasses, scheduleOverlayForCourse,
} from './CourseSelectionWorkspacePage';
import { useCachedResource } from '../resources/ResourceStore';
import {
  checkJwxkCatalogEligibility, getJwxkCatalogFilterOptions, getJwxkSelected, getJwxkStatus,
  readJwxkPlan, searchJwxkCatalog,
} from '../services/api';

let mockTimetableProps;

jest.mock('../services/api', () => ({
  checkJwxkCatalogEligibility: jest.fn(),
  getJwxkCatalogFilterOptions: jest.fn(),
  getJwxkSelected: jest.fn(),
  getJwxkStatus: jest.fn(),
  readJwxkPlan: jest.fn(),
  searchJwxkCatalog: jest.fn(),
}));
jest.mock('../resources/ResourceStore', () => ({ useCachedResource: jest.fn() }));
jest.mock('./TimetablePage', () => props => {
  mockTimetableProps = props;
  return null;
});
jest.mock('../components/CourseOutlineDrawer', () => () => null);

test('selected experiment time is added to the timetable overlay without adding alternatives', () => {
  const overlay = scheduleOverlayForCourse({
    class_id: 'class', course_code: 'C1', course_name: '用户体验', schedules: [],
    experiment_schedules: [
      { schedule_id: 'exp-1', project_name: '实验一', weekday: 4, start_section: 7, end_section: 8, weeks: [13] },
      { schedule_id: 'exp-2', project_name: '实验一', weekday: 5, start_section: 7, end_section: 8, weeks: [13] },
    ],
    selected_experiment_schedule_ids: ['exp-2'],
  }, 'preview', 'test');

  expect(overlay).toHaveLength(1);
  expect(overlay[0]).toMatchObject({
    course_name: '用户体验 · 实验一', weekday: 5, course_type: '实验',
  });
  expect(overlay[0].tags).toContain('实验预览');
});

describe('selection workspace independent resource loading', () => {
  let root;
  let container;
  let resource;
  const batch = {
    code: 'batch', name: '选修轮次', state: 'ended', selection_type_code: '02',
    term_code: '2026-2027-1',
  };
  const groups = [{
    group_id: 'course', course_code: 'C1', course_name: '测试选修课',
    classes: [{
      class_id: 'class', course_code: 'C1', course_name: '测试选修课',
      schedules: [{ weekday: 1, start_section: 1, end_section: 2, weeks: [1] }],
    }],
  }];
  const render = async () => {
    await act(async () => {
      root.render(<MemoryRouter initialEntries={['/selection/batch']}>
        <Routes><Route path="/selection/:batchCode" element={<CourseSelectionWorkspacePage />} /></Routes>
      </MemoryRouter>);
    });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 150)); });
  };

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    mockTimetableProps = null;
    window.matchMedia = jest.fn(() => ({
      matches: false, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    }));
    resource = { data: null, loading: true, syncState: 'running' };
    useCachedResource.mockImplementation(() => resource);
    readJwxkPlan.mockResolvedValue({ batch_code: 'batch', batch, items: [], groups: [] });
    getJwxkStatus.mockResolvedValue({ batches: [batch] });
    getJwxkSelected.mockResolvedValue({ selected: [], volunteered: [] });
    searchJwxkCatalog.mockImplementation(async payload => ({
      cache_hit: payload.local_only, groups, total: 1, scope: 'ALL',
    }));
    checkJwxkCatalogEligibility.mockResolvedValue({
      results: [{ class_id: 'class', status: 'selectable' }],
    });
    getJwxkCatalogFilterOptions.mockResolvedValue({
      course_natures: [], course_categories: [], general_elective_categories: [],
      campuses: [], departments: [], sections: [], scopes: [],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  test('report loading does not block the catalog, and completion does not reload the workspace', async () => {
    await render();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(container.textContent).toContain('测试选修课');
    expect(readJwxkPlan).toHaveBeenCalledTimes(1);
    expect(checkJwxkCatalogEligibility).toHaveBeenCalledWith(
      'batch', ['class'], { skipAuthRedirect: true },
    );
    const searches = searchJwxkCatalog.mock.calls.length;
    resource = { data: { categories: [] }, loading: false };
    await render();
    expect(readJwxkPlan).toHaveBeenCalledTimes(1);
    expect(searchJwxkCatalog).toHaveBeenCalledTimes(searches);
  });

  test('failed background catalog read is visible instead of masquerading as no courses', async () => {
    searchJwxkCatalog.mockImplementation(async payload => {
      if (payload.local_only) return { cache_hit: true, groups: [], total: 0, scope: 'ALL' };
      throw new Error('尚未取得本轮课程范围');
    });
    await render();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(container.textContent).toContain('尚未取得本轮课程范围');
    expect(container.textContent).not.toContain('当前条件下没有课程');
  });

  test('keeps special filters inside the more-filters modal', async () => {
    await render();
    expect(container.textContent).not.toContain('不上时间');
    const moreFilters = [...container.querySelectorAll('button')]
      .find(button => button.textContent.includes('更多筛选'));
    await act(async () => {
      moreFilters.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(document.body.textContent).toContain('课程筛选');
    expect(document.body.textContent).toContain('特殊筛选');
    expect(document.body.textContent).not.toContain('不上时间');
    expect(document.body.textContent).toContain('不上早八');
    expect(document.body.textContent).toContain('不上晚十');
    expect(document.body.textContent).toContain('不上实验');
  });

  test('catalog refresh also asks the embedded personal timetable to rebuild its conflict baseline', async () => {
    await render();
    const initialSignal = mockTimetableProps.refreshSignal;
    const refresh = container.querySelector('.jwxk-header-refresh');
    await act(async () => {
      refresh.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 20));
    });
    expect(mockTimetableProps.refreshSignal).toBe(initialSignal + 1);
  });

  test('ignores a personal-timetable callback for a different selection term', async () => {
    await render();
    const staleCallback = mockTimetableProps.onPersonalCoursesChange;
    await act(async () => {
      staleCallback([{ course_code: 'OLD' }], { ready: true, termCode: '2025-2026-2' });
    });
    expect(container.textContent).not.toContain('OLD');
    expect(mockTimetableProps.onPersonalCoursesChange).toBe(staleCallback);
  });

  test('clears only the removed courses from saved experiment times', () => {
    expect(clearExperimentSelectionsForClasses(['class'], {
      class: ['exp-1'], other: ['exp-2'],
    })).toEqual({ other: ['exp-2'] });
  });
});
