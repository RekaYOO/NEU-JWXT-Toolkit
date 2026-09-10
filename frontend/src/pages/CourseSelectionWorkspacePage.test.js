import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import CourseSelectionWorkspacePage from './CourseSelectionWorkspacePage';
import { useCachedResource } from '../resources/ResourceStore';
import {
  checkJwxkCatalogEligibility, getJwxkSelected, getJwxkStatus,
  readJwxkPlan, searchJwxkCatalog,
} from '../services/api';

jest.mock('../services/api', () => ({
  checkJwxkCatalogEligibility: jest.fn(),
  getJwxkSelected: jest.fn(),
  getJwxkStatus: jest.fn(),
  readJwxkPlan: jest.fn(),
  searchJwxkCatalog: jest.fn(),
}));
jest.mock('../resources/ResourceStore', () => ({ useCachedResource: jest.fn() }));
jest.mock('./TimetablePage', () => () => null);
jest.mock('../components/CourseOutlineDrawer', () => () => null);

describe('selection workspace independent resource loading', () => {
  let root;
  let container;
  let resource;
  const batch = { code: 'batch', name: '选修轮次', state: 'ended', selection_type_code: '02' };
  const groups = [{
    group_id: 'course', course_code: 'C1', course_name: '测试选修课',
    classes: [{ class_id: 'class', course_code: 'C1', course_name: '测试选修课', schedules: [] }],
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
});
