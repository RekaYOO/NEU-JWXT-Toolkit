import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SchedulePreviewControls from './SchedulePreviewControls';

describe('schedule preview controls', () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  test('shows a concise cancellation action for one course', async () => {
    const onCancel = jest.fn();
    const course = { id: 'one', course_name: '设计基础' };
    await act(async () => root.render(
      <SchedulePreviewControls courses={[course]} courseKey={item => item.id} onCancel={onCancel} />,
    ));
    const button = container.querySelector('button');
    expect(container.querySelector('[role="region"]').getAttribute('aria-label')).toBe('课表预览操作');
    expect(button.textContent).toBe('取消预览');
    expect(button.getAttribute('aria-label')).toContain('设计基础');
    await act(async () => button.click());
    expect(onCancel).toHaveBeenCalledWith(course);
  });

  test('keeps separate cancellation actions for multiple courses', async () => {
    const onCancel = jest.fn();
    const courses = [
      { id: 'one', course_name: '设计基础' },
      { id: 'two', course_name: '信息检索' },
    ];
    await act(async () => root.render(
      <SchedulePreviewControls courses={courses} courseKey={item => item.id} onCancel={onCancel} />,
    ));
    expect(container.textContent).toContain('正在预览 2 门');
    const buttons = container.querySelectorAll('button');
    expect(buttons).toHaveLength(2);
    await act(async () => buttons[1].click());
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledWith(courses[1]);
  });
});
