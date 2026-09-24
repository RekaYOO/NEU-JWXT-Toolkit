import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ExperimentCoursePage from './ExperimentCoursePage';

let mockSnapshot = null;
const mockRefresh = jest.fn();
const mockReloadCache = jest.fn();
jest.mock('../resources/ResourceStore', () => ({
  useCachedResource: resource => resource === 'experiment-courses'
    ? {
      data: mockSnapshot,
      refresh: mockRefresh,
      reloadAndApply: jest.fn().mockResolvedValue(mockSnapshot),
      reloadCache: mockReloadCache,
    }
    : { data: null },
  useResourceOfflineMode: () => false,
  useResourceIdentity: () => 'test-account',
}));
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => jest.fn(),
}));

describe('experiment selection cache-first states', () => {
  let root;
  let container;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = () => ({
      matches: false, addListener() {}, removeListener() {},
      addEventListener() {}, removeEventListener() {},
    });
    mockSnapshot = null;
    mockRefresh.mockReset();
    mockReloadCache.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('cached selection remains visible if remote refresh fails', async () => {
    mockSnapshot = {
      term: '2026-2027-1',
      courses: [],
      selected_results: [{
        task_id: 'task-1', project_code: 'project-1',
        selected_round_id: 'round-1', course_name: '示例实验课',
        project_name: '必做项目', select_status: '已选',
      }],
    };
    mockRefresh.mockRejectedValue(new Error('remote unavailable'));
    await act(async () => root.render(<ExperimentCoursePage />));
    expect(container.textContent).toContain('示例实验课');
    expect(container.textContent).toContain('实验课程读取失败');
    expect(container.textContent).not.toContain('当前没有已形成的实验选课结果');
  });

  test('first refresh failure is not presented as a confirmed empty result', async () => {
    mockRefresh.mockRejectedValue(new Error('remote unavailable'));
    await act(async () => root.render(<ExperimentCoursePage />));
    expect(container.textContent).toContain('实验课程读取失败');
    expect(container.textContent).toContain('实验选课结果尚未确认');
    expect(container.textContent).not.toContain('当前没有已形成的实验选课结果');
  });
});
