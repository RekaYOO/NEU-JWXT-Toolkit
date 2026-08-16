import {
  courseCardContent,
  courseIsPreselected,
  courseMatchesWeek,
  courseVisibleLines,
  formatWeekNumbers,
  immediateNextTerm,
  layoutDayCourses,
  groupDayCourses,
  clusterLayoutMetrics,
  clusterStackLayout,
  clusterDisplayCapacity,
  adaptiveSectionHeights,
  selectionSectionHeights,
  selectionCompactCourseHeight,
  selectionClusterRequiredHeight,
  estimatedCourseCardHeight,
  estimatedFoldedCourseHeight,
  shouldUsePersonalTimetableCache,
  shouldUsePersonalTimetableEndpoint,
  mergeTargetOptions,
  mergeTargetFilterOptions,
  sortGradeOptionsNewestFirst,
  sortTargetsByRecentGrade,
  facetTargetFilterOptions,
  targetFilterMissingParent,
  updateTargetFilterDraft,
  preserveModeSessionsForTermChange,
  personalScheduleView,
  preferredMobileDay,
  isCourseHappeningNow,
  shouldHighlightToday,
  selectDefaultTerm,
  selectEffectiveCurrentTerm,
  selectDefaultWeek,
  shouldLoadMoreTargets,
  capacityRangeInvalid,
  conflictCandidateFromCourse,
  personalConflictMapFromResponse,
  mergeScheduleWithSelectionOverlays,
  requestErrorText,
  restorePersonalTimetableMemory,
  usableTargetFilterDefinitions,
  TIMETABLE_DAY_ORDER,
  TIMETABLE_MODES,
  MobileTimetable,
} from './TimetablePage';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

jest.mock('../services/api', () => ({
  getTimetableContext: jest.fn(),
  getPersonalTimetable: jest.fn(),
  getTimetableSchedule: jest.fn(),
  getTimetableTargetFilterOptions: jest.fn(),
  getTimetableTerms: jest.fn(),
  searchTimetableTargets: jest.fn(),
  checkScheduleConflicts: jest.fn(),
}));


describe('TimetablePage helpers', () => {
  test('recognizes the official preselection tag for blue course names', () => {
    expect(courseIsPreselected({ tags: ['必修', '预选'] })).toBe(true);
    expect(courseIsPreselected({ preselected: true, tags: [] })).toBe(true);
    expect(courseIsPreselected({ tags: ['必修'] })).toBe(false);
  });
  test('mobile timetable renders without reading parent-only embedded props', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(<MobileTimetable
          coursesByDay={Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]))}
          sections={[]}
          selectedDay={1}
          viewMode="term"
          currentTerm={false}
          currentWeekNumber={1}
          onDayChange={() => {}}
          onCourseClick={() => {}}
          personalConflictMap={{}}
        />);
      });
      expect(container.querySelector('.timetable-mobile')).not.toBeNull();
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('explains frontend request timeouts instead of reporting an unknown term failure', () => {
    expect(requestErrorText(
      { code: 'ECONNABORTED', message: 'timeout of 30000ms exceeded' },
      '无法读取课表学期，请稍后重试',
      '课表学期读取等待超时，可能有其他教务任务正在执行，请重试',
    )).toBe('课表学期读取等待超时，可能有其他教务任务正在执行，请重试');
    expect(requestErrorText(
      { code: 'ECONNABORTED' },
      '搜索查询对象失败，请重试',
    )).toBe('搜索查询对象失败，请重试');
    expect(requestErrorText(
      { response: { data: { detail: '后端明确错误' } } },
      'fallback',
    )).toBe('后端明确错误');
  });

  test('defaults the product modes to personal then class, teacher and room', () => {
    expect(TIMETABLE_MODES.map(item => item.key)).toEqual([
      'personal', 'class', 'teacher', 'room',
    ]);
  });

  test('removes an already selected overlay from the first timetable frame', () => {
    const personal = [{
      id: 'personal-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, start_section: 1, end_section: 2,
    }];
    const overlays = [{
      id: 'selected-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, start_section: 1, end_section: 2, layer: 'selected',
    }, {
      id: 'candidate-1', course_code: 'B200', course_name: '候选课程',
      weekday: 2, start_section: 3, end_section: 4, layer: 'candidate',
    }];

    expect(mergeScheduleWithSelectionOverlays(personal, overlays).map(item => item.id)).toEqual([
      'personal-1', 'candidate-1',
    ]);
  });

  test('pending weight overlay replaces the same baseline course so it remains blue', () => {
    const personal = [{
      id: 'personal-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, start_section: 1, end_section: 2,
    }];
    const overlays = [{
      id: 'pending-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, start_section: 1, end_section: 2, layer: 'pending',
    }];

    expect(mergeScheduleWithSelectionOverlays(personal, overlays).map(item => item.id)).toEqual([
      'pending-1',
    ]);
  });

  test('candidate preview replaces the same baseline course instead of duplicating it', () => {
    const personal = [{
      id: 'personal-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, start_section: 1, end_section: 2,
    }];
    const overlays = [{
      id: 'candidate-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, start_section: 1, end_section: 2, layer: 'candidate',
    }];
    expect(mergeScheduleWithSelectionOverlays(personal, overlays).map(item => item.id)).toEqual([
      'candidate-1',
    ]);
  });

  test('does not mark duplicate representations of the same course as a conflict', () => {
    const grouped = groupDayCourses([{
      id: 'personal-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, weeks: [1, 2], start_section: 1, end_section: 2,
    }, {
      id: 'selected-1', course_code: 'A100', course_name: '物流与供应链管理',
      weekday: 1, weeks: [1, 2], start_section: 1, end_section: 2, layer: 'selected',
    }]);

    expect(grouped[0].courses.every(course => course.hasActualConflict === false)).toBe(true);
    expect(layoutDayCourses(grouped[0].courses).every(course => course.hasActualConflict === false)).toBe(true);
  });

  test('uses timetable cache only for the personal current term', () => {
    expect(shouldUsePersonalTimetableCache('personal', '2026-2027-1', '2026-2027-1')).toBe(true);
    expect(shouldUsePersonalTimetableCache('personal', '2025-2026-2', '2026-2027-1')).toBe(false);
    expect(shouldUsePersonalTimetableCache('personal', '2027-2028-1', '2026-2027-1', '2027-2028-1')).toBe(true);
    expect(shouldUsePersonalTimetableCache('class', '2026-2027-1', '2026-2027-1')).toBe(false);
  });

  test('embedded selection timetable loads its known term before term discovery finishes', () => {
    expect(shouldUsePersonalTimetableEndpoint(
      'personal', '2026-2027-1', '', '',
      { embedded: true, preferredTermCode: '2026-2027-1' },
    )).toBe(true);
    expect(shouldUsePersonalTimetableEndpoint(
      'personal', '2026-2027-1', '', '',
      { embedded: true, preferredTermCode: '2025-2026-2' },
    )).toBe(false);
    expect(shouldUsePersonalTimetableEndpoint(
      'teacher', '2026-2027-1', '', '',
      { embedded: true, preferredTermCode: '2026-2027-1' },
    )).toBe(false);
  });

  test('builds a bounded conflict candidate from the displayed meeting', () => {
    expect(conflictCandidateFromCourse({
      id: 'row-1', meeting_id: 'meeting-1', course_name: '软件工程', course_code: 'C-1',
      weeks: [1, 3], weekday: 2, start_section: 3, end_section: 4,
      teachers: ['不应传输'], title_details: ['不应传输'],
    })).toEqual(expect.objectContaining({
      candidate_id: 'meeting-1', course_name: '软件工程', course_code: 'C-1',
      weeks: [1, 3], weekday: 2, start_section: 3, end_section: 4,
    }));
    expect(conflictCandidateFromCourse({ id: 'row-1', course_name: '课程' })).not.toHaveProperty('teachers');
  });

  test('keeps only hard personal-timetable matches for red conflict markers', () => {
    const mapped = personalConflictMapFromResponse({ results: [{
      candidate_id: 'meeting-1',
      status: 'conflict',
      matches: [
        { status: 'conflict', baseline_course_name: '高等数学' },
        { status: 'unknown', baseline_course_name: '周次不完整课程' },
      ],
    }] });
    expect(mapped['meeting-1'].matches).toEqual([
      { status: 'conflict', baseline_course_name: '高等数学' },
    ]);
  });

  test('restores the current personal timetable from identity-scoped memory on remount', () => {
    const payload = {
      term_code: '2026-2027-1',
      campuses: [{ code: 'all', name: '全部校区' }],
      weeks: [{ number: 1, name: '第1周', current: true }],
      sections_by_campus: { all: [{ number: 1, name: '第1节' }] },
      courses: [{ id: 'course-1', campus_code: 'all', weekday: 1, start_section: 1, end_section: 1, weeks: [1] }],
      unscheduled: [],
      practices: [],
    };
    const restored = restorePersonalTimetableMemory({
      terms: [{ code: '2026-2027-1', name: '秋季学期', current: true }],
      currentTermCode: '2026-2027-1',
      payload,
      campusCode: 'all',
      weekNumber: 1,
      viewMode: 'week',
    });

    expect(restored.termCode).toBe('2026-2027-1');
    expect(restored.schedule.courses).toHaveLength(1);
    expect(restorePersonalTimetableMemory(
      { currentTermCode: '2026-2027-1', payload },
      '2025-2026-2',
    )).toBeNull();
  });

  test('places overlapping courses in separate lanes without splitting later courses unnecessarily', () => {
    const result = layoutDayCourses([
      { id: 'a', start_section: 1, end_section: 2 },
      { id: 'b', start_section: 2, end_section: 3 },
      { id: 'c', start_section: 4, end_section: 5 },
    ]);
    const byId = Object.fromEntries(result.map(item => [item.id, item]));

    expect(byId.a.laneCount).toBe(2);
    expect(byId.b.laneCount).toBe(2);
    expect(byId.a.lane).not.toBe(byId.b.lane);
    expect(byId.c.laneCount).toBe(1);
  });

  test('keeps disjoint-week courses visible in separate lanes without calling them conflicts', () => {
    const result = layoutDayCourses([
      { id: 'a', start_section: 1, end_section: 2, weeks: [1, 3], recurrence_unknown: false },
      { id: 'b', start_section: 1, end_section: 2, weeks: [2, 4], recurrence_unknown: false },
    ]);

    expect(result.every(item => item.laneCount === 2)).toBe(true);
    expect(result.map(item => item.lane)).toEqual([0, 1]);
    expect(result.every(item => item.hasActualConflict === false)).toBe(true);
  });

  test('marks same-week courses in overlapping sections as actual conflicts', () => {
    const result = layoutDayCourses([
      { id: 'a', start_section: 1, end_section: 2, weeks: [1, 3], recurrence_unknown: false },
      { id: 'b', start_section: 2, end_section: 3, weeks: [3, 4], recurrence_unknown: false },
    ]);

    expect(result.every(item => item.hasActualConflict)).toBe(true);
  });

  test('groups same-slot courses vertically without widening the seven-day grid', () => {
    const groups = groupDayCourses([
      { id: 'a', start_section: 1, end_section: 1, weeks: [1] },
      { id: 'b', start_section: 1, end_section: 1, weeks: [2] },
      { id: 'c', start_section: 1, end_section: 1, weeks: [1] },
      { id: 'd', start_section: 1, end_section: 1, weeks: [3] },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].courses).toHaveLength(4);
    expect(groups[0].courses.find(item => item.id === 'a').hasActualConflict).toBe(true);
    expect(groups[0].courses.find(item => item.id === 'b').hasActualConflict).toBe(false);
    expect(clusterDisplayCapacity(106)).toBe(2);
    expect(clusterDisplayCapacity(442)).toBeGreaterThanOrEqual(4);
  });

  test('uses the longest course as the default base when concurrent courses start together', () => {
    const groups = groupDayCourses([
      { id: 'short', start_section: 1, end_section: 2, weeks: [12] },
      { id: 'long-b', start_section: 1, end_section: 4, weeks: [2, 3, 4] },
      { id: 'long-a', start_section: 1, end_section: 4, weeks: [7, 8, 9] },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].courses.map(course => course.id)).toEqual(['long-a', 'long-b', 'short']);
    expect(groups[0].courses[0].end_section).toBe(4);
  });

  test('expands the hovered course in sequence and folds cards above and below it', () => {
    const crowded = clusterLayoutMetrics(666, 18);
    expect(crowded.visibleCourseCount).toBeGreaterThanOrEqual(14);
    expect(crowded.expandedHeight).toBeGreaterThanOrEqual(96);
    expect(crowded.expandedHeight).toBeGreaterThan(crowded.foldedHeight);
    expect(crowded.hasHiddenCourses).toBe(true);
    const stack = clusterStackLayout(crowded, crowded.visibleCourseCount, 6, true);
    expect(stack.courses[5].expanded).toBe(false);
    expect(stack.courses[6].expanded).toBe(true);
    expect(stack.courses[7].expanded).toBe(false);
    expect(stack.courses[6].top).toBe(stack.courses[5].top + crowded.foldedHeight + crowded.gap);
    expect(stack.courses[7].top).toBeGreaterThan(stack.courses[6].top);
    expect(stack.courses[7].top + stack.courses[7].height).toBeLessThanOrEqual(
      stack.courses[6].top + stack.courses[6].height,
    );

    const roomy = clusterLayoutMetrics(218, 2);
    expect(roomy.visibleCourseCount).toBe(2);
    expect(roomy.expandedHeight).toBeGreaterThanOrEqual(120);
    expect(roomy.foldedHeight).toBe(40);
  });

  test('grows only the rows that need more content instead of fixing every section to one height', () => {
    const longSingleSection = {
      id: 'long',
      weekday: 1,
      start_section: 1,
      end_section: 1,
      course_name: '机械设计基础齿轮传动效率测试分析综合实验课程',
      location: '浑南校区机电学馆235第6实验班',
      teacher: '教师甲',
      course_type: '必修 · 考试',
      weeks: [1, 2, 3, 4],
    };
    const shortTwoSection = {
      id: 'short',
      weekday: 2,
      start_section: 1,
      end_section: 2,
      course_name: '技术经济学',
      location: '信息A114',
      teacher: '教师乙',
      course_type: '必修 · 考试',
      weeks: [1, 2, 3, 4],
    };
    const heights = adaptiveSectionHeights(
      [{ number: 1 }, { number: 2 }],
      { 1: [longSingleSection], 2: [shortTwoSection] },
      'term',
      'personal',
    );
    expect(heights[0]).toBeGreaterThan(64);
    expect(heights[1]).toBe(64);
    expect(heights[0]).toBeGreaterThanOrEqual(estimatedCourseCardHeight(longSingleSection, 'term', 'personal'));
    expect(heights[0] + heights[1]).toBeGreaterThanOrEqual(estimatedCourseCardHeight(shortTwoSection, 'term', 'personal'));
  });

  test('selection timetable sizes concurrent courses as one expanded card plus folded cards', () => {
    const concurrentCourses = [
      { id: 'a', start_section: 1, end_section: 1, weeks: [1] },
      { id: 'b', start_section: 1, end_section: 1, weeks: [2] },
      { id: 'c', start_section: 1, end_section: 1, weeks: [3] },
    ];
    const heights = selectionSectionHeights(
      [{ number: 1 }, { number: 2 }],
      {
        1: concurrentCourses,
        2: [{ id: 'single', start_section: 2, end_section: 2, weeks: [1] }],
      },
    );

    expect(heights[0]).toBeGreaterThanOrEqual(selectionClusterRequiredHeight(concurrentCourses));
    expect(heights[1]).toBe(48);
    expect(selectionCompactCourseHeight({ course_name: '短课名' })).toBe(44);
    expect(selectionCompactCourseHeight({ course_name: '非常非常长的课程名称用于验证多行标题高度' })).toBeGreaterThan(44);

    const metrics = clusterLayoutMetrics(
      heights[0] - 6,
      concurrentCourses.length,
      concurrentCourses.map(selectionCompactCourseHeight),
      { minimumFoldedHeight: 44, minimumExpandedHeight: 56 },
    );
    expect(metrics.visibleCourseCount).toBe(concurrentCourses.length);
    expect(metrics.hasHiddenCourses).toBe(false);
    const stack = clusterStackLayout(metrics, concurrentCourses.length, 1, false);
    expect(stack.courses.map(item => item.expanded)).toEqual([false, true, false]);
    expect(stack.courses[0].top).toBeLessThan(stack.courses[1].top);
    expect(stack.courses[2].top).toBeGreaterThan(stack.courses[1].top);
  });

  test('gives folded cards enough height to show the complete course title', () => {
    const shortCourse = { course_name: '技术经济学', start_section: 5, end_section: 6 };
    const longCourse = {
      course_name: '毛泽东思想和中国特色社会主义理论体系概论',
      start_section: 7,
      end_section: 8,
    };

    expect(estimatedFoldedCourseHeight(longCourse, 'term')).toBeGreaterThan(
      estimatedFoldedCourseHeight(shortCourse, 'term'),
    );
    expect(estimatedFoldedCourseHeight(shortCourse, 'term')).toBeGreaterThanOrEqual(40);
  });

  test('keeps the card hierarchy stable instead of trusting official text order', () => {
    const course = {
      course_name: '课程名',
      campus: '浑南校区',
      location: '示例楼101',
      course_nature: '必修',
      assessment_type: '考试',
      cell_details: ['教师甲', '1-8周', '课程名'],
    };
    expect(courseCardContent(course)).toEqual({
      name: '课程名',
      location: '浑南校区 · 示例楼101',
      type: '必修 · 考试',
    });
    expect(courseVisibleLines(course)).toEqual(['课程名', '浑南校区 · 示例楼101', '必修 · 考试']);
  });

  test('detects current term and prefers teaching-week date boundaries over stale flags', () => {
    const terms = [
      { code: '2025-2026-2', name: '2025-2026学年第二学期' },
      { code: '2026-2027-1', name: '2026-2027学年第一学期', current: true },
    ];
    expect(selectDefaultTerm(terms, '')).toBe('2026-2027-1');
    expect(selectDefaultTerm(terms, '2025-2026-2')).toBe('2025-2026-2');
    expect(immediateNextTerm(terms, '2025-2026-2')).toBe('2026-2027-1');

    const weeks = [
      { number: 1, start_date: '2026-08-31', end_date: '2026-09-06' },
      { number: 2, start_date: '2026-09-07', end_date: '2026-09-13', current: true },
    ];
    expect(selectDefaultWeek(weeks, { now: new Date('2026-09-02T12:00:00') })).toBe(1);
    expect(selectDefaultWeek(weeks, { now: new Date('2026-10-02T12:00:00') })).toBe(2);
  });

  test('switches teaching weeks at Sunday and displays Sunday first', () => {
    const weeks = [
      { number: 1, start_date: '2026-08-30', end_date: '2026-09-05' },
      { number: 2, start_date: '2026-09-06', end_date: '2026-09-12' },
    ];

    expect(TIMETABLE_DAY_ORDER).toEqual([7, 1, 2, 3, 4, 5, 6]);
    expect(selectDefaultWeek(weeks, { now: new Date('2026-09-05T12:00:00') })).toBe(1);
    expect(selectDefaultWeek(weeks, { now: new Date('2026-09-06T00:01:00') })).toBe(2);
  });

  test('uses a date-inferred term as the effective current term', () => {
    const terms = [
      { code: '2025-2026-1', name: '2025-2026学年第一学期' },
      { code: '2025-2026-2', name: '2025-2026学年第二学期' },
    ];
    const now = new Date('2026-03-15T12:00:00');

    expect(selectEffectiveCurrentTerm(terms, '', now)).toBe('2025-2026-2');
    expect(selectDefaultTerm(terms, '', now)).toBe('2025-2026-2');
  });

  test('filters a cached personal timetable locally by week and campus', () => {
    const payload = {
      term_code: '2026-2027-1',
      campuses: [{ code: 'all', name: '全部校区' }, { code: 'HN', name: '浑南校区' }],
      courses: [
        { id: 'a', campus: '浑南校区', weeks: [1, 3], recurrence_unknown: false },
        { id: 'b', campus: '南湖校区', weeks: [2], recurrence_unknown: false },
        { id: 'c', campus: '浑南校区', weeks: [], recurrence_unknown: true },
      ],
      unscheduled: [],
      practices: [],
    };
    expect(courseMatchesWeek(payload.courses[0], 3)).toBe(true);
    expect(courseMatchesWeek(payload.courses[0], 2)).toBe(false);
    expect(courseMatchesWeek(payload.courses[2], 2)).toBe(true);
    expect(personalScheduleView(payload, 'HN', 'week', 2).courses.map(item => item.id)).toEqual(['c']);
    expect(personalScheduleView(payload, 'all', 'term', null).courses).toHaveLength(3);
    expect(formatWeekNumbers([1, 2, 3, 5, 7, 8])).toBe('1–3、5、7–8 周');
  });

  test('opens the closest teaching day when today has no classes', () => {
    expect(preferredMobileDay({ 1: [{ id: 'a' }], 2: [], 3: [{ id: 'b' }], 4: [], 5: [], 6: [], 7: [] }, 2)).toBe(1);
  });

  test('marks a course as in progress only in the current term and teaching week', () => {
    const course = {
      weekday: 7,
      weeks: [1, 3],
      recurrence_unknown: false,
      start_time: '09:50',
      end_time: '11:30',
    };
    const now = new Date('2026-08-09T10:20:00');

    expect(isCourseHappeningNow(course, { now, currentTerm: true, currentWeekNumber: 3 })).toBe(true);
    expect(isCourseHappeningNow(course, { now, currentTerm: true, currentWeekNumber: 2 })).toBe(false);
    expect(isCourseHappeningNow(course, { now, currentTerm: false, currentWeekNumber: 3 })).toBe(false);
    expect(isCourseHappeningNow({ ...course, end_time: '10:00' }, { now, currentTerm: true, currentWeekNumber: 3 })).toBe(false);
  });

  test('shows today only for the current term current week or current-term overview', () => {
    const current = {
      termCode: '2026-2027-1',
      currentTermCode: '2026-2027-1',
      currentWeekNumber: 3,
    };
    expect(shouldHighlightToday({ ...current, viewMode: 'week', weekNumber: 3 })).toBe(true);
    expect(shouldHighlightToday({ ...current, viewMode: 'week', weekNumber: 2 })).toBe(false);
    expect(shouldHighlightToday({ ...current, viewMode: 'term', weekNumber: 2 })).toBe(true);
    expect(shouldHighlightToday({ ...current, termCode: '2025-2026-2', viewMode: 'term', weekNumber: 3 })).toBe(false);
  });

  test('appends every remote target page while de-duplicating stable ids', () => {
    expect(mergeTargetOptions(
      [{ id: 'a', name: 'old' }, { id: 'b', name: 'second' }],
      [{ id: 'a', name: 'new' }, { id: 'c', name: 'third' }],
    )).toEqual([
      { id: 'a', name: 'new' },
      { id: 'b', name: 'second' },
      { id: 'c', name: 'third' },
    ]);
    expect(shouldLoadMoreTargets({
      loading: false,
      loaded: 30,
      total: 91,
      scrollTop: 700,
      clientHeight: 300,
      scrollHeight: 1000,
    })).toBe(true);
    expect(shouldLoadMoreTargets({
      loading: false,
      loaded: 91,
      total: 91,
      scrollTop: 700,
      clientHeight: 300,
      scrollHeight: 1000,
    })).toBe(false);
  });

  test('shows recent grades and class targets before older years', () => {
    expect(sortGradeOptionsNewestFirst([
      { value: '23', label: '2023级' },
      { value: '2026', label: '2026级' },
      { value: '25', label: '2025级' },
    ]).map(item => item.label)).toEqual(['2026级', '2025级', '2023级']);

    expect(sortTargetsByRecentGrade([
      { id: '2024', details: { grade: '2024级' } },
      { id: '2026', details: { grade: '2026级' } },
      { id: '2025', filter_values: { grade: '25' } },
    ]).map(item => item.id)).toEqual(['2026', '2025', '2024']);
  });

  test('validates a complete classroom capacity range', () => {
    expect(capacityRangeInvalid({ min_capacity: 60, max_capacity: 120 })).toBe(false);
    expect(capacityRangeInvalid({ min_capacity: 120, max_capacity: 60 })).toBe(true);
    expect(capacityRangeInvalid({ min_capacity: 60 })).toBe(false);
  });

  test('keeps every official filter category visible even when its catalog is empty', () => {
    const definitions = [['department', '单位'], ['title', '职称'], ['gender', '性别']];
    const options = { department: [{ value: '01', label: '学院' }], title: [], gender: [] };
    expect(usableTargetFilterDefinitions(definitions, options, {}, {}, true)).toEqual(definitions);
    expect(usableTargetFilterDefinitions(definitions, options, { title: '02' }, {}, true)).toEqual(definitions);
  });

  test('merges the complete filter catalog with values discovered on loaded rows', () => {
    expect(mergeTargetFilterOptions(
      [{ value: '14', label: '工商管理学院' }, { value: '08', label: '计算机学院' }],
      [{ value: '14', label: '工商管理学院' }, { value: '01', label: '文法学院' }],
    )).toEqual([
      { value: '14', label: '工商管理学院' },
      { value: '08', label: '计算机学院' },
      { value: '01', label: '文法学院' },
    ]);
  });

  test('facets class majors and directions by the selected college raw code', () => {
    const catalog = [
      { value: '1401', label: '工业工程' },
      { value: '0801', label: '软件工程' },
    ];
    const relations = [
      { college: '14', major: '1401', direction: 'A' },
      { college: '08', major: '0801', direction: 'B' },
    ];

    expect(facetTargetFilterOptions('major', catalog, relations, { college: '14' }))
      .toEqual([{ value: '1401', label: '工业工程' }]);
    expect(updateTargetFilterDraft(
      'class',
      { college: '08', major: '0801', direction: 'B' },
      'college',
      '14',
    )).toEqual({ college: '14' });
  });

  test('lets a higher-level filter change even when a lower-level value would conflict', () => {
    const grades = [{ value: '2025', label: '2025级' }, { value: '2026', label: '2026级' }];
    const relations = [
      { grade: '2025', college: '14', major: 'classic' },
      { grade: '2026', college: '14', major: 'automation' },
    ];
    const order = ['grade', 'college', 'major', 'direction', 'campus'];

    expect(facetTargetFilterOptions(
      'grade', grades, relations, { major: 'automation' }, order,
    )).toEqual(grades);
    expect(updateTargetFilterDraft(
      'class',
      { grade: '2026', college: '14', major: 'automation' },
      'grade',
      '2025',
      relations,
      order,
    )).toEqual({ grade: '2025', college: '14' });
    expect(targetFilterMissingParent('class', 'major', {})).toBe('college');
    expect(targetFilterMissingParent('class', 'major', { college: '14' })).toBe('');
  });

  test('preserves filters and keywords across terms while discarding term-specific targets and catalogs', () => {
    const sessions = {
      class: {
        target: { id: 'class-a' },
        options: [{ id: 'class-a' }],
        filterOptions: { grade: [{ value: '2025', label: '2025级' }] },
        filterRelations: [{ grade: '2025' }],
        filters: { grade: '2025', college: '14' },
        search: { keyword: '工业工程', page: 3, total: 120 },
      },
    };

    expect(preserveModeSessionsForTermChange(sessions)).toEqual({
      class: {
        target: null,
        options: [],
        filterOptions: {},
        filterRelations: [],
        filterOptionsLoadedFor: '',
        filters: { grade: '2025', college: '14' },
        search: { keyword: '工业工程', page: 0, total: 0 },
      },
    });
  });
});
