import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { CourseDetail } from './TimetablePage';
import { scheduleOverlayForCourse } from './CourseSelectionWorkspacePage';
import { archiveOverlay } from './CourseSelectionArchivePage';
import { selectionTimetableMetadata } from '../utils/jwxkSchedule';

const source = {
  class_id: 'class-1', course_code: 'C1', course_name: '测试课程',
  course_nature: '通识选修', exam_type: '考查', score_scale: '百分制',
  schedules: [{ weekday: 1, start_section: 1, end_section: 2, weeks: [1, 2], location: '教学楼101' }],
};
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

test.each(['preview', 'candidate', 'pending', 'selected'])('%s keeps academic fields separate from display status', layer => {
  const [meeting] = scheduleOverlayForCourse(source, layer, 'fixture');
  expect(meeting.course_nature).toBe('通识选修');
  expect(meeting.course_type).toBe('通识选修');
  expect(meeting.assessment_type).toBe('考查');
  expect(meeting.grading_scheme).toBe('百分制');
  expect(meeting.teaching_class_id).toBe('class-1');
  expect(meeting.weeks).toEqual([1, 2]);
});

test('archived overlays preserve metadata without live detail requests', () => {
  expect(archiveOverlay(source)[0]).toMatchObject({
    course_type: '通识选修', course_nature: '通识选修', assessment_type: '考查', grading_scheme: '百分制',
  });
  expect(selectionTimetableMetadata({}, source)).toEqual({
    course_nature: '通识选修', assessment_type: '考查', grading_scheme: '百分制',
  });
});

describe('preview detail enrichment', () => {
  let container;
  let root;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
    global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });
  const render = async (course, resolver, isMobile = false) => act(async () => {
    root.render(<CourseDetail course={course} resolveCourseDetail={resolver} isMobile={isMobile} />);
  });
  const property = label => [...document.querySelectorAll('.ant-descriptions-row')]
    .find(row => row.textContent.includes(label))?.textContent;

  test.each([false, true])('loads actual academic metadata and retains meeting fields (mobile=%s)', async mobile => {
    const [meeting] = scheduleOverlayForCourse({ ...source, course_nature: '', exam_type: '', score_scale: '' }, 'preview', 'fixture');
    const request = deferred();
    const resolver = jest.fn(() => request.promise);
    await render(meeting, resolver, mobile);
    expect(property('课程性质')).not.toContain('正在预览');
    expect(document.body.textContent).toContain('正在读取课程详情');
    await act(async () => request.resolve(selectionTimetableMetadata(source)));
    expect(property('课程性质')).toContain('通识选修');
    expect(property('考核方式')).toContain('考查');
    expect(property('成绩类型')).toContain('百分制');
    expect(document.body.textContent).toContain('教学楼101');
    expect(document.body.textContent).toContain('正在预览');
    expect(resolver).toHaveBeenCalledTimes(1);
  });

  test('late detail cannot replace another selected course', async () => {
    const [first] = scheduleOverlayForCourse(source, 'preview', 'fixture');
    const second = { ...first, id: 'second', course_name: '另一课程', course_nature: '必修' };
    const old = deferred();
    const resolver = jest.fn().mockReturnValueOnce(old.promise).mockResolvedValueOnce({ course_nature: '专业必修' });
    await render(first, resolver);
    await render(second, resolver);
    await act(async () => old.resolve({ course_nature: '错误旧结果' }));
    expect(property('课程性质')).toContain('专业必修');
    expect(document.body.textContent).not.toContain('错误旧结果');
  });

  test('failure retains known fields and supports retry; personal courses do not fetch', async () => {
    const [meeting] = scheduleOverlayForCourse(source, 'candidate', 'fixture');
    const resolver = jest.fn().mockRejectedValueOnce(new Error('fixture')).mockResolvedValueOnce({ assessment_type: '考试' });
    await render(meeting, resolver);
    expect(property('考核方式')).toContain('考查');
    expect(document.body.textContent).toContain('已保留现有信息');
    await act(async () => [...document.querySelectorAll('button')].find(button => button.textContent.replace(/\s/g, '') === '重试').click());
    expect(property('考核方式')).toContain('考试');
    await render({ ...meeting, layer: undefined }, resolver);
    expect(resolver).toHaveBeenCalledTimes(2);
    await render(null, resolver);
    expect(resolver).toHaveBeenCalledTimes(2);
  });
});
