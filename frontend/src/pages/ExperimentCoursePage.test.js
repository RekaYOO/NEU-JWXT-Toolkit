import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ExperimentCoursePage from './ExperimentCoursePage';
import { Modal } from 'antd';
import { getExperimentRounds, selectExperimentRound, verifyExperimentOperation } from '../services/api';

jest.mock('../services/api', () => ({
  ...jest.requireActual('../services/api'),
  getExperimentRounds: jest.fn(), selectExperimentRound: jest.fn(), verifyExperimentOperation: jest.fn(),
}));

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
    getExperimentRounds.mockReset();
    selectExperimentRound.mockReset();
    verifyExperimentOperation.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    jest.restoreAllMocks();
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

  test('selected results lead with a quick overview and schedule details', async () => {
    mockSnapshot = {
      term: '2026-2027-1', courses: [], selected_results: [
        {
          task_id: 'task-1', project_code: 'project-1', selected_round_id: 'round-1',
          course_name: '材料实验', project_name: '必做项目', week: '3-4', day: '二',
          time: '3-4', location: '实验楼 A203', round_name: '一班',
        },
        { task_id: 'task-2', project_code: 'project-2', selected_round_id: 'round-2', course_name: '待安排实验' },
      ],
    };
    await act(async () => root.render(<ExperimentCoursePage />));
    expect(container.textContent).toContain('已选实验');
    expect(container.textContent).toContain('已有安排');
    expect(container.textContent).toContain('实验楼 A203');
    expect(container.textContent).toContain('第3-4周');
  });

  test('long selected-result fields use constrained value elements', async () => {
    mockSnapshot = {
      term: '2026-2027-1',
      courses: [],
      selected_results: [{
        task_id: 'task-long', project_code: 'project-long', selected_round_id: 'round-long',
        course_name: '基础工业工程与生产物流系统综合实验课程名称较长',
        project_name: '工业工程方向综合实验项目名称', round_name: '一班',
        location: '生产与物流系统综合实验室(文管学馆B201)【文管学院】',
        select_start: '2026-08-14 16:50:53', select_end: '2026-08-14 16:52:00',
      }],
    };

    await act(async () => root.render(<ExperimentCoursePage />));

    expect(container.querySelector('.experiment-selected-result__course-title')).not.toBeNull();
    expect(container.querySelectorAll('.experiment-selected-result__value').length).toBe(2);
    expect([...container.querySelectorAll('.experiment-selected-result__value')]
      .every(element => element.classList.contains('experiment-selected-result__value'))).toBe(true);
  });

  test('first refresh failure is not presented as a confirmed empty result', async () => {
    mockRefresh.mockRejectedValue(new Error('remote unavailable'));
    await act(async () => root.render(<ExperimentCoursePage />));
    expect(container.textContent).toContain('实验课程读取失败');
    expect(container.textContent).toContain('实验选课结果尚未确认');
    expect(container.textContent).not.toContain('当前没有已形成的实验选课结果');
  });

  test('unknown selection exposes verification without resubmitting', async () => {
    mockSnapshot = { term: '2026-2027-1', selected_results: [], courses: [{ task_id: 'task', course_name: '实验课程', course_no: 'course', selected_count: 0, must_do_count: 1, projects: [{ project_code: 'project', project_name: '实验项目', is_selected: false }] }] };
    getExperimentRounds.mockResolvedValue({ rounds: [{ wid: 'round', round_name: '实验班', can_select: true, conflict_status: 'unknown', selected_count: 0, capacity: 20 }] });
    selectExperimentRound.mockResolvedValue({ code: '-1', state: 'unknown', msg: '结果待核验，不会重复提交' });
    verifyExperimentOperation.mockResolvedValue({ code: '0', state: 'confirmed', msg: '官方结果已确认' });
    let confirmation;
    jest.spyOn(Modal, 'confirm').mockImplementation(options => { confirmation = options; return { destroy() {} }; });
    await act(async () => root.render(<ExperimentCoursePage />));
    await act(async () => container.querySelector('.ant-collapse-header').click());
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent.replace(/\s/g, '') === '选课').click());
    await act(async () => [...document.querySelectorAll('button')].find(button => button.textContent.includes('确认后选择')).click());
    expect(selectExperimentRound).not.toHaveBeenCalled();
    await act(async () => confirmation.onOk());
    expect(container.textContent).toContain('实验操作结果待核验');
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '核验操作结果').click());
    expect(selectExperimentRound).toHaveBeenCalledTimes(1);
    expect(verifyExperimentOperation).toHaveBeenCalledWith({ term: '2026-2027-1', task_id: 'task', project_code: 'project', round_id: 'round' });
    expect(container.textContent).not.toContain('实验操作结果待核验');
  });
});
