import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import useCourseOutlineMetadata from './useCourseOutlineMetadata';
import {
  getCourseOutlineMetadata,
  getCourseOutlineMetadataSyncStatus,
  startCourseOutlineMetadataSync,
} from '../services/api';

jest.mock('../services/api', () => ({
  getCourseOutlineMetadata: jest.fn(),
  getCourseOutlineMetadataSyncStatus: jest.fn(),
  startCourseOutlineMetadataSync: jest.fn(),
}));

const COURSES = [{ course_code: 'A100', course_name: '测试课程' }];

const Harness = ({ onState, courses = COURSES, enabled = true, offlineMode = false }) => {
  const state = useCourseOutlineMetadata({
    courses, enabled, offlineMode,
  });
  onState(state);
  return null;
};

const flushPromises = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('useCourseOutlineMetadata', () => {
  let container;
  let root;
  let latest;

  beforeEach(() => {
    jest.resetAllMocks();
    getCourseOutlineMetadata.mockResolvedValue({ items: [] });
    getCourseOutlineMetadataSyncStatus.mockResolvedValue({ running: false, errors: [] });
    jest.useFakeTimers();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    latest = null;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
    jest.useRealTimers();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('同步启动后继续轮询，并在终态读取已保存元数据', async () => {
    getCourseOutlineMetadata
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({
        items: [{
          course_code: 'A100',
          assessment_method: '考试',
          grading_scale: '百分制',
          status: 'success',
        }],
      });
    getCourseOutlineMetadataSyncStatus
      .mockResolvedValueOnce({ running: false, total: 0, completed: 0, failed: 0 })
      .mockResolvedValueOnce({ running: false, total: 1, completed: 1, failed: 0 });
    startCourseOutlineMetadataSync.mockResolvedValue({
      running: true, total: 1, completed: 0, failed: 0,
    });

    await act(async () => {
      root.render(<Harness onState={state => { latest = state; }} />);
    });
    await flushPromises();

    expect(latest.syncing).toBe(true);
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledTimes(1);

    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    await flushPromises();

    expect(latest.syncing).toBe(false);
    expect(latest.metadata.A100).toMatchObject({
      assessment_method: '考试',
      grading_scale: '百分制',
    });
    expect(getCourseOutlineMetadataSyncStatus).toHaveBeenCalledTimes(2);
  });

  test('状态接口连续失败后结束加载并保留错误状态', async () => {
    getCourseOutlineMetadataSyncStatus.mockRejectedValue(new Error('network unavailable'));

    await act(async () => {
      root.render(<Harness onState={state => { latest = state; }} />);
    });
    await flushPromises();
    expect(latest.syncing).toBe(true);

    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    await flushPromises();
    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    await flushPromises();

    expect(latest.syncing).toBe(false);
    expect(latest.status).toMatchObject({
      running: false,
      error: 'network unavailable',
    });
    expect(getCourseOutlineMetadataSyncStatus).toHaveBeenCalledTimes(3);
  });

  const render = async (props = {}) => {
    await act(async () => {
      root.render(<Harness onState={state => { latest = state; }} {...props} />);
    });
    await flushPromises();
  };

  const tick = async (ms = 5000) => {
    await act(async () => { jest.advanceTimersByTime(ms); });
    await flushPromises();
  };

  const saved = (code = 'A100', scale = '百分制') => ({
    course_code: code, assessment_method: '考试', grading_scale: scale, status: 'success',
  });

  test('已有缓存不等待失败的状态接口，也不提交任务', async () => {
    getCourseOutlineMetadata.mockResolvedValue({ items: [saved()] });
    getCourseOutlineMetadataSyncStatus.mockRejectedValue(new Error('unavailable'));
    await render();
    expect(latest.metadata.A100.grading_scale).toBe('百分制');
    expect(latest.syncing).toBe(false);
    expect(getCourseOutlineMetadataSyncStatus).not.toHaveBeenCalled();
    expect(startCourseOutlineMetadataSync).not.toHaveBeenCalled();
  });

  test('保留过期缓存显示，同时补全后端标记需要重新核验的课程', async () => {
    getCourseOutlineMetadata.mockResolvedValue({ items: [{ ...saved(), needs_sync: true }] });
    startCourseOutlineMetadataSync.mockResolvedValue({ running: true, accepted: true });
    await render();
    expect(latest.metadata.A100.grading_scale).toBe('百分制');
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledWith(COURSES, false);
    getCourseOutlineMetadata.mockResolvedValue({ items: [{ ...saved('A100', '五级制'), needs_sync: false }] });
    await tick();
    expect(latest.syncing).toBe(false);
    expect(latest.metadata.A100.grading_scale).toBe('五级制');
  });

  test('共享批次运行期间先显示已有缓存，不逐课程重读', async () => {
    getCourseOutlineMetadata.mockResolvedValue({ items: [saved()] });
    getCourseOutlineMetadataSyncStatus.mockResolvedValue({ running: true, completed: 1 });
    await render({ courses: [...COURSES, { code: 'B200' }] });
    expect(latest.metadata.A100.grading_scale).toBe('百分制');
    expect(latest.syncing).toBe(true);
    await tick();
    expect(getCourseOutlineMetadata).toHaveBeenCalledTimes(1);
    expect(startCourseOutlineMetadataSync).not.toHaveBeenCalled();
  });

  test('共享批次结束后只提交本页仍缺失的课程', async () => {
    getCourseOutlineMetadata.mockResolvedValueOnce({ items: [] })
      .mockResolvedValue({ items: [saved()] });
    getCourseOutlineMetadataSyncStatus.mockResolvedValueOnce({ running: true })
      .mockResolvedValue({ running: false });
    startCourseOutlineMetadataSync.mockResolvedValue({ running: true });
    await render({ courses: [...COURSES, { code: 'B200' }] });
    await tick();
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledWith([{ code: 'B200' }], false);
  });

  test('提交时碰到另一页面刚启动的批次，等待后仍补交本页缺失项', async () => {
    startCourseOutlineMetadataSync.mockResolvedValueOnce({ running: true, accepted: false })
      .mockResolvedValue({ running: true, accepted: true });
    await render();
    await tick();
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledTimes(2);
    getCourseOutlineMetadata.mockResolvedValue({ items: [saved()] });
    await tick();
    expect(latest.metadata.A100.grading_scale).toBe('百分制');
    expect(latest.syncing).toBe(false);
  });

  test('提交连续失败不会被成功的状态 GET 重置失败计数', async () => {
    startCourseOutlineMetadataSync.mockRejectedValue(new Error('submit failed'));
    await render();
    await tick();
    await tick();
    expect(latest.syncing).toBe(false);
    expect(latest.status).toMatchObject({ failed: 1, errors: ['A100'], error: 'submit failed' });
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledTimes(3);
    await tick(30000);
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledTimes(3);
  });

  test('缓存读取失败不当作缓存缺失提交远端任务，并可重试恢复', async () => {
    getCourseOutlineMetadata.mockRejectedValue(new Error('cache unavailable'));
    await render();
    await tick();
    await tick();
    expect(latest.syncing).toBe(false);
    expect(startCourseOutlineMetadataSync).not.toHaveBeenCalled();
    getCourseOutlineMetadata.mockResolvedValue({ items: [] });
    startCourseOutlineMetadataSync.mockResolvedValue({ running: true });
    await act(async () => { await latest.retryFailed(); });
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledWith(COURSES, true);
    getCourseOutlineMetadata.mockResolvedValue({ items: [saved()] });
    await tick();
    expect(latest.syncing).toBe(false);
    expect(latest.metadata.A100.grading_scale).toBe('百分制');
  });

  test('终态缓存暂时读取失败后继续读取，不能静默宣告成功', async () => {
    startCourseOutlineMetadataSync.mockResolvedValue({ running: true });
    getCourseOutlineMetadata.mockResolvedValueOnce({ items: [] })
      .mockRejectedValueOnce(new Error('cache busy'))
      .mockResolvedValue({ items: [saved()] });
    await render();
    await tick();
    expect(latest.syncing).toBe(true);
    await tick();
    expect(latest.syncing).toBe(false);
    expect(latest.metadata.A100.grading_scale).toBe('百分制');
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledTimes(1);
  });

  test('失败项重试后继续监控，不在终态重复提交', async () => {
    startCourseOutlineMetadataSync.mockResolvedValue({ running: true });
    await render();
    getCourseOutlineMetadataSyncStatus.mockResolvedValue({
      running: false, failed: 1, errors: ['A100'],
    });
    await tick();
    expect(latest.syncing).toBe(false);
    await act(async () => { await latest.retryFailed(); });
    expect(startCourseOutlineMetadataSync).toHaveBeenLastCalledWith(COURSES, true);
    getCourseOutlineMetadataSyncStatus.mockResolvedValue({ running: false, failed: 0, errors: [] });
    getCourseOutlineMetadata.mockResolvedValue({ items: [saved()] });
    await tick();
    expect(latest.syncing).toBe(false);
    expect(latest.metadata.A100.grading_scale).toBe('百分制');
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledTimes(2);
  });

  test('终态之后仍接收其他页面触发的缓存更新', async () => {
    getCourseOutlineMetadata.mockResolvedValue({ items: [saved()] });
    await render();
    getCourseOutlineMetadata.mockResolvedValue({ items: [saved('A100', '五级制')] });
    await act(async () => {
      window.dispatchEvent(new CustomEvent('neu-cache-event', {
        detail: { resource: 'course-outline-metadata', variant: 'course:A100' },
      }));
    });
    await tick(100);
    expect(latest.metadata.A100.grading_scale).toBe('五级制');
    expect(startCourseOutlineMetadataSync).not.toHaveBeenCalled();
  });

  test('后台长期无进展时结束加载，仍允许用户重试', async () => {
    getCourseOutlineMetadataSyncStatus.mockResolvedValue({
      running: true, total: 1, completed: 0, failed: 0, current_course: 'A100',
    });
    await render();
    for (let i = 0; i < 26; i += 1) await tick();
    expect(latest.syncing).toBe(false);
    expect(latest.status.error).toContain('无进展');
    expect(latest.status.errors).toEqual(['A100']);
  });

  test.each([{ enabled: false }, { offlineMode: true }, { courses: [] }])(
    '禁用、离线或空列表不读取也不提交 %j', async props => {
      await render(props);
      expect(latest.syncing).toBe(false);
      expect(getCourseOutlineMetadata).not.toHaveBeenCalled();
      expect(getCourseOutlineMetadataSyncStatus).not.toHaveBeenCalled();
      expect(startCourseOutlineMetadataSync).not.toHaveBeenCalled();
    },
  );

  test('关闭列后忽略尚未完成的旧读取，不再启动后台同步', async () => {
    let resolveRead;
    getCourseOutlineMetadata.mockImplementationOnce(() => new Promise(resolve => { resolveRead = resolve; }));
    await render();
    await render({ enabled: false });
    await act(async () => { resolveRead({ items: [saved()] }); });
    expect(latest.metadata).toEqual({});
    expect(latest.syncing).toBe(false);
    expect(startCourseOutlineMetadataSync).not.toHaveBeenCalled();
  });

  test('再次启用时服从服务器缓存删除，不复用旧内存条目', async () => {
    getCourseOutlineMetadata.mockResolvedValueOnce({ items: [saved()] });
    await render();
    await render({ enabled: false });
    startCourseOutlineMetadataSync.mockResolvedValue({ running: true });
    await render();
    expect(latest.metadata).toEqual({});
    expect(startCourseOutlineMetadataSync).toHaveBeenCalledWith(COURSES, false);
  });
});
