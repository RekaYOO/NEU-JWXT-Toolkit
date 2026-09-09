import {
  courseCardContent,
  courseIsPreselected,
  selectionCourseStateLabel,
  courseMatchesWeek,
  courseVisibleLines,
  formatWeekNumbers,
  immediateNextTerm,
  groupDayCourses,
  clusterLayoutMetrics,
  clusterStackLayout,
  clusterDisplayCapacity,
  adaptiveSectionHeights,
  mobileCompactSectionHeights,
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
  mobileCourseSummary,
  adjacentMobileTimetableDay,
  adjacentMobileTimetableWeek,
  mobileInitialFocusAnchor,
  timetableCacheIndicator,
  timetableRecoveryNoticeClassName,
  courseTeacherText,
  courseClassText,
  mobileCourseContext,
  formatTimetableFloor,
  shouldRefreshTimetableTargets,
  timetableMobileContextText,
  queryTimetableScheduleMatches,
  shouldShowQueryTimetablePending,
  timetableSectionAxisMinimumHeight,
  shouldHighlightToday,
  selectDefaultTerm,
  selectEffectiveCurrentTerm,
  selectDefaultWeek,
  automaticTimetableNotice,
  shouldLoadMoreTargets,
  capacityRangeInvalid,
  conflictCandidateFromCourse,
  conflictMeetingText,
  buildConflictCourseScheduleMap,
  personalConflictMapFromResponse,
  mergeScheduleWithSelectionOverlays,
  requestErrorText,
  TIMETABLE_LOGIN_ERROR_TEXT,
  restorePersonalTimetableMemory,
  timetableSnapshotIsNewer,
  timetableContentSignature,
  timetableContentChanged,
  usableTargetFilterDefinitions,
  TIMETABLE_DAY_ORDER,
  TIMETABLE_MODES,
  MobileTimetable,
  MobileCompactWeekTimetable,
  MobileTimetableSummary,
  TimetableGrid,
  mobileWeekRailScrollLeft,
} from './TimetablePage';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

jest.mock('../services/api', () => ({
  getTimetableContext: jest.fn(),
  getPersonalTimetable: jest.fn(),
  getTimetableSchedule: jest.fn(),
  getTimetableTargetFilterOptions: jest.fn(),
  getTimetableTerms: jest.fn(),
  getTimetableBootstrap: jest.fn().mockResolvedValue({ terms: [], current: null, personal: [] }),
  syncTimetable: jest.fn().mockResolvedValue({ jobs: [] }),
  searchTimetableTargets: jest.fn(),
  checkScheduleConflicts: jest.fn(),
}));


describe('TimetablePage helpers', () => {
  test('uses the shared selection overlay labels without losing the round-specific selected state', () => {
    expect(selectionCourseStateLabel({ layer: 'candidate' })).toBe('方案候选');
    expect(selectionCourseStateLabel({ layer: 'pending' })).toBe('已投权待结果');
    expect(selectionCourseStateLabel({ layer: 'preview' })).toBe('正在预览');
    expect(selectionCourseStateLabel({
      layer: 'selected', tags: ['已抢到课程'],
    })).toBe('已抢到课程');
    expect(selectionCourseStateLabel({ layer: 'selected' })).toBe('已选课程');
  });

  test('centers the selected week inside the horizontal rail without document scrolling', () => {
    const rail = { scrollWidth: 900, clientWidth: 300 };
    const active = { offsetLeft: 420, offsetWidth: 90 };
    expect(mobileWeekRailScrollLeft(rail, active)).toBe(315);
    expect(mobileWeekRailScrollLeft({ ...rail, scrollWidth: 320 }, active)).toBe(20);
  });

  test('accepts a newer server snapshot but never rolls back to an older one', () => {
    const current = { cache: { revision: 'a', last_checked_at: '2026-09-01T10:00:00Z', saved_at: '2026-09-01T09:59:00Z' } };
    const newer = { cache: { revision: 'b', last_checked_at: '2026-09-01T10:01:00Z', saved_at: '2026-09-01T10:00:00Z' } };
    const older = { cache: { revision: 'c', last_checked_at: '2026-09-01T09:58:00Z', saved_at: '2026-09-01T09:57:00Z' } };
    expect(timetableSnapshotIsNewer(newer, current)).toBe(true);
    expect(timetableSnapshotIsNewer(older, current)).toBe(false);
  });

  test('compares timetable content separately from cache metadata', () => {
    const current = {
      term_code: '2026-2027-1',
      courses: [{ course_name: '课程A', weekday: 1, start_section: 1 }],
      cache: { revision: 'a', last_checked_at: '2026-09-01T10:00:00Z' },
    };
    const metadataOnly = {
      ...current,
      cache: { revision: 'b', last_checked_at: '2026-09-01T10:01:00Z' },
    };
    const changed = {
      ...metadataOnly,
      courses: [{ course_name: '课程B', weekday: 1, start_section: 1 }],
    };
    expect(timetableContentSignature(metadataOnly)).toBe(timetableContentSignature(current));
    expect(timetableContentChanged(metadataOnly, current)).toBe(false);
    expect(timetableContentChanged(changed, current)).toBe(true);
  });

  test('maps timetable cache state to the refresh button indicator', () => {
    expect(timetableCacheIndicator({ source: 'browser' }).state).toBe('local');
    expect(timetableCacheIndicator({ source: 'server', payload: { is_fresh: false } }).state).toBe('server');
    expect(timetableCacheIndicator({ source: 'server', payload: { is_fresh: true } }).state).toBe('fresh');
    expect(timetableCacheIndicator({
      source: 'server',
      payload: { cache: { last_error_kind: 'remote_error' } },
    }).state).toBe('error');
  });

  test('places the recovery notice above the desktop timetable and below the mobile timetable', () => {
    expect(timetableRecoveryNoticeClassName(false)).toContain('notice-top');
    expect(timetableRecoveryNoticeClassName(true)).toContain('notice-below');
  });

  test('uses an honest preselection state and refreshes the query target list', () => {
    expect(shouldRefreshTimetableTargets({ mode: 'teacher', target: null })).toBe(true);
    expect(shouldRefreshTimetableTargets({ mode: 'room', target: { id: 'R1' } })).toBe(false);
    expect(shouldRefreshTimetableTargets({ mode: 'personal', target: null })).toBe(false);
    expect(timetableMobileContextText({
      mode: 'teacher', target: null, viewMode: 'week',
    })).toBe('选择教师后读取教学周和校区');
    expect(timetableMobileContextText({
      mode: 'teacher', target: { id: 'T1' }, viewMode: 'week',
      selectedWeekName: '第3周', selectedCampusName: '浑南校区',
    })).toBe('第3周 · 浑南校区');
  });

  test('formats integer classroom floors without changing special floor labels', () => {
    expect(formatTimetableFloor('3.0')).toBe('3');
    expect(formatTimetableFloor('12.000')).toBe('12');
    expect(formatTimetableFloor('B1')).toBe('B1');
    expect(formatTimetableFloor('3.5')).toBe('3.5');
  });

  test('keeps normal current-week auto detection silent', () => {
    expect(automaticTimetableNotice({ hasCurrentCourses: true })).toBe('');
  });

  test('focuses the week rail for weekly browsing and the weekday selector for term browsing', () => {
    const weekAnchor = document.createElement('div');
    const dayAnchor = document.createElement('div');
    expect(mobileInitialFocusAnchor('week', weekAnchor, dayAnchor)).toBe(weekAnchor);
    expect(mobileInitialFocusAnchor('term', weekAnchor, dayAnchor)).toBe(dayAnchor);
  });

  test('conflict details fall back from an empty weeks array to known baseline weeks', () => {
    expect(conflictMeetingText({
      weeks: [], baseline_weeks: [11, 13], overlapping_weeks: [11],
      weekday: 1, start_section: 7, end_section: 8,
    })).toContain('11、13 周');
  });

  test('builds complete split-course schedules for conflict comparison', () => {
    const schedules = buildConflictCourseScheduleMap([
      { course_code: 'A1', course_name: '分段课程', weeks: [1, 2], weekday: 2, start_section: 1, end_section: 2 },
      { course_code: 'A1', course_name: '分段课程', weeks: [5, 6], weekday: 4, start_section: 7, end_section: 8 },
      { course_code: 'A1', course_name: '分段课程', weeks: [1, 2], weekday: 2, start_section: 1, end_section: 2 },
    ]);

    expect(schedules['name:分段课程']).toHaveLength(2);
    expect(schedules['name:分段课程']).toEqual(expect.arrayContaining([
      expect.objectContaining({ weeks: [1, 2], weekday: 2, start_section: 1, end_section: 2 }),
      expect.objectContaining({ weeks: [5, 6], weekday: 4, start_section: 7, end_section: 8 }),
    ]));
  });

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

  test('renders a teacher row for ordinary mobile course cards', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(<MobileTimetable
          coursesByDay={Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [
            day,
            day === 1 ? [{
              id: 'teacher-course', course_name: '示例课程', teachers: ['教师甲'],
              weekday: 1, weeks: [3], start_section: 1, end_section: 2,
              start_time: '08:00', end_time: '09:40', location: '教学楼101',
            }] : [],
          ]))}
          sections={[]}
          selectedDay={1}
          viewMode="week"
          currentTerm
          currentWeekNumber={3}
          onDayChange={() => {}}
          onCourseClick={() => {}}
          personalConflictMap={{}}
        />);
      });
      expect(container.querySelector('.mobile-course-teacher').textContent).toBe('教师甲');
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('uses mode-aware context rows for queried mobile course cards', async () => {
    expect(courseClassText({ classes: ['工业工程2401', '工业工程2401'] }))
      .toBe('工业工程2401');
    expect(mobileCourseContext({ teachers: ['教师甲'], classes: ['班级甲'] }, 'class'))
      .toEqual({ teacher: '教师甲', classes: '' });
    expect(mobileCourseContext({ teachers: ['教师甲'], classes: ['班级甲'] }, 'teacher'))
      .toEqual({ teacher: '', classes: '班级甲' });
    expect(mobileCourseContext({ teachers: ['教师甲'], classes: ['班级甲'] }, 'room'))
      .toEqual({ teacher: '教师甲', classes: '班级甲' });

    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const coursesByDay = Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, day === 1 ? [{
      id: 'teacher-query-course', course_name: '示例课程', teachers: ['当前教师'],
      classes: ['工业工程2401'], weekday: 1, weeks: [3], start_section: 1,
      end_section: 2, location: '信息楼101',
    }] : []]));
    try {
      await act(async () => {
        root.render(<MobileTimetable
          coursesByDay={coursesByDay}
          sections={[]}
          selectedDay={1}
          viewMode="week"
          mode="teacher"
          currentTerm
          currentWeekNumber={3}
          onDayChange={() => {}}
          onCourseClick={() => {}}
          personalConflictMap={{}}
        />);
      });
      expect(container.querySelector('.mobile-course-classes').textContent).toBe('工业工程2401');
      expect(container.querySelector('.mobile-course-teacher')).toBeNull();
      expect(container.textContent).not.toContain('当前教师');
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('distinguishes an unloaded queried timetable from a confirmed empty response', () => {
    const target = { id: 'T001', name: '教师甲' };
    const selection = {
      mode: 'teacher', targetId: target.id, termCode: '2026-2027-1',
      campusCode: '01', viewMode: 'week', weekNumber: 3,
    };
    const confirmedEmpty = {
      mode: 'teacher', target_id: target.id, term_code: '2026-2027-1',
      campus_code: '01', week: 3, courses: [], unscheduled: [], practices: [],
    };
    expect(queryTimetableScheduleMatches({ ...selection, schedule: confirmedEmpty }))
      .toBe(true);
    expect(queryTimetableScheduleMatches({
      ...selection, schedule: { ...confirmedEmpty, week: 2 },
    })).toBe(false);
    expect(shouldShowQueryTimetablePending({ mode: 'teacher', target, scheduleMatches: false, loading: true }))
      .toBe(true);
    expect(shouldShowQueryTimetablePending({ mode: 'teacher', target, scheduleMatches: false, loading: false }))
      .toBe(false);
    expect(shouldShowQueryTimetablePending({
      mode: 'teacher', target, scheduleMatches: true,
    })).toBe(false);
    expect(shouldShowQueryTimetablePending({
      mode: 'teacher', target, scheduleMatches: false, loading: true, error: { message: '读取失败' },
    })).toBe(false);
    expect(shouldShowQueryTimetablePending({ mode: 'personal', target, scheduleMatches: false }))
      .toBe(false);
  });

  test('shows current course details above timetable settings when the summary expands', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(<MobileTimetableSummary
          summary={{
            kind: 'next',
            course: {
              course_name: '示例课程',
              start_section: 3,
              end_section: 4,
              start_time: '10:30',
              end_time: '12:10',
              campus: '浑南校区',
              location: '信息楼A112',
              teachers: ['教师甲', '教师乙'],
            },
            startTime: '10:30',
          }}
          defaultTimetableOnOpen={false}
          onToggleDefault={() => {}}
          compactWeekView={false}
          onToggleCompactWeekView={() => {}}
          viewMode="week"
          onViewModeChange={() => {}}
        />);
      });
      const trigger = container.querySelector('.timetable-mobile-summary-trigger');
      const chevron = trigger.querySelector('.timetable-mobile-summary-chevron');
      expect(chevron.querySelector('svg[data-icon="down"]')).not.toBeNull();
      expect(chevron.getAttribute('aria-hidden')).toBe('true');
      expect(trigger.getAttribute('aria-expanded')).toBe('false');
      expect(chevron.classList.contains('is-expanded')).toBe(false);
      await act(async () => trigger.click());
      expect(trigger.getAttribute('aria-expanded')).toBe('true');
      expect(chevron.classList.contains('is-expanded')).toBe(true);
      const detail = container.querySelector('.timetable-mobile-summary-course');
      expect(detail.textContent).toContain('10:30–12:10 · 第3–4节');
      expect(detail.textContent).toContain('浑南校区 · 信息楼A112');
      expect(detail.textContent).toContain('教师甲、教师乙');
      expect(detail.compareDocumentPosition(
        container.querySelector('.timetable-mobile-summary-default'),
      ) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      const preferences = container.querySelector('.timetable-mobile-summary-preferences');
      expect(preferences.querySelectorAll('label')).toHaveLength(2);
      expect(preferences.textContent).toContain('打开时默认课表');
      expect(preferences.textContent).toContain('使用缩略视图');
      await act(async () => trigger.click());
      expect(trigger.getAttribute('aria-expanded')).toBe('false');
      expect(chevron.classList.contains('is-expanded')).toBe(false);
      expect(container.querySelector('.timetable-mobile-summary-controls')).toBeNull();
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('shows a query target first and exposes shared view and conflict controls', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(<MobileTimetableSummary
          mode="teacher"
          target={{ id: 'T001', name: '教师甲' }}
          targetDescription="计算机学院 · 教授"
          summary={{
            kind: 'next',
            course: {
              course_name: '数据结构', start_section: 3, end_section: 4,
              start_time: '10:30', end_time: '12:10', location: '信息楼A112',
              teachers: ['教师甲'], classes: ['计算机类2401'],
            },
          }}
          compactWeekView
          onToggleCompactWeekView={() => {}}
          viewMode="week"
          onViewModeChange={() => {}}
          conflictDetectionEnabled
          onToggleConflictDetection={() => {}}
        />);
      });
      const trigger = container.querySelector('.timetable-mobile-summary-trigger');
      expect(trigger.textContent).toContain('教师');
      expect(trigger.textContent).toContain('教师甲');
      expect(trigger.textContent).not.toContain('数据结构');
      await act(async () => trigger.click());
      expect(container.querySelector('.timetable-mobile-summary-target').textContent)
        .toContain('T001');
      expect(container.querySelector('.timetable-mobile-summary-target').textContent)
        .toContain('计算机学院 · 教授');
      expect(container.querySelector('.timetable-mobile-summary-course').textContent)
        .toContain('数据结构');
      expect(container.querySelector('.timetable-mobile-summary-course').textContent)
        .toContain('计算机类2401');
      expect(container.querySelector('.timetable-mobile-summary-course').textContent)
        .not.toContain('教师甲');
      const preferences = container.querySelector('.timetable-mobile-summary-preferences');
      expect(preferences.textContent).toContain('使用缩略视图');
      expect(preferences.textContent).toContain('与我的课表冲突');
      expect(preferences.textContent).not.toContain('打开时默认课表');
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('explains frontend request timeouts instead of reporting an unknown term failure', () => {
    expect(requestErrorText(
      { response: { status: 401, data: { detail: '登录状态已失效，请重新登录' } } },
      'fallback',
    )).toBe(TIMETABLE_LOGIN_ERROR_TEXT);
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
      {
        status: 'conflict', baseline_course_name: '高等数学',
        baseline_weeks: [], overlapping_weeks: [],
      },
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

  test('grows compact mobile rows until stacked courses can show a complete location', () => {
    const heights = mobileCompactSectionHeights(
      [{ number: 1 }, { number: 2 }],
      {
        1: [
          {
            id: 'left',
            start_section: 1,
            end_section: 1,
            course_name: '课程甲',
            location: '浑南校区信息学馆A区第一公共实验室',
          },
          {
            id: 'right',
            start_section: 1,
            end_section: 1,
            course_name: '课程乙',
            location: '浑南校区建筑学馆东侧第二阶梯教室',
          },
        ],
      },
    );

    expect(heights[0]).toBeGreaterThan(52);
    expect(heights[1]).toBe(52);
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

  test('only reports exceptional automatic timetable fallbacks', () => {
    expect(automaticTimetableNotice({ hasCurrentCourses: true })).toBe('');
    expect(automaticTimetableNotice({ nextTermHasCourses: true })).toContain('下一学期');
    expect(automaticTimetableNotice({ nextTermLoadFailed: true })).toContain('暂无法核验');
    expect(automaticTimetableNotice()).toContain('无课周');
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

  test('derives the mobile today summary from the current teaching week', () => {
    const courses = [
      {
        course_name: '下午课程', weekday: 1, weeks: [3], start_time: '16:30', end_time: '18:00',
      },
      {
        course_name: '当前课程', weekday: 1, weeks: [3], start_time: '10:00', end_time: '11:30',
      },
      {
        course_name: '未知周次', weekday: 1, weeks: [], start_time: '09:00', end_time: '10:00',
      },
    ];
    const now = new Date('2026-08-17T10:30:00');
    expect(mobileCourseSummary(courses, {
      now, currentTerm: true, currentWeekNumber: 3,
    })).toEqual(expect.objectContaining({
      kind: 'current',
      label: '当前',
      course: expect.objectContaining({ course_name: '当前课程' }),
    }));
    expect(mobileCourseSummary(courses, {
      now: new Date('2026-08-17T12:00:00'), currentTerm: true, currentWeekNumber: 3,
    })).toEqual(expect.objectContaining({
      kind: 'next',
      label: '下节',
      startTime: '16:30',
      course: expect.objectContaining({ course_name: '下午课程' }),
    }));
    expect(mobileCourseSummary(courses, {
      now: new Date('2026-08-17T19:00:00'), currentTerm: true, currentWeekNumber: 3,
    })).toEqual(expect.objectContaining({ kind: 'complete', label: '今明两天课程结束' }));
  });

  test('shows Monday classes on a class-free Sunday without advancing the teaching week', () => {
    const weeks = [
      { number: 1, start_date: '2026-08-30', end_date: '2026-09-05' },
      { number: 2, start_date: '2026-09-06', end_date: '2026-09-12' },
    ];
    const now = new Date('2026-09-06T20:00:00');
    const courses = [
      { course_name: '明日晚课', weekday: 1, weeks: [2], start_time: '16:30', end_time: '18:00' },
      { course_name: '上周课程', weekday: 1, weeks: [1], start_time: '07:00', end_time: '08:00' },
      { course_name: '明日早课', weekday: 1, weeks: [2], start_time: '08:30', end_time: '10:00' },
    ];
    expect(mobileCourseSummary(courses, {
      now,
      currentTerm: true,
      currentWeekNumber: selectDefaultWeek(weeks, { now }),
      weeks,
    })).toEqual(expect.objectContaining({
      kind: 'tomorrow',
      label: '明日',
      startTime: '08:30',
      course: expect.objectContaining({ course_name: '明日早课' }),
    }));
  });

  test.each([
    ['2026-09-06', 1, 2],
    ['2026-09-07', 2, 2],
    ['2026-09-08', 3, 2],
    ['2026-09-09', 4, 2],
    ['2026-09-10', 5, 2],
    ['2026-09-11', 6, 2],
    ['2026-09-12', 7, 3],
  ])('finds the next calendar day from %s with the correct teaching week', (date, weekday, week) => {
    const course = {
      course_name: '明日课程', weekday, weeks: [week], start_time: '08:30', end_time: '10:00',
    };
    expect(mobileCourseSummary([course], {
      now: new Date(`${date}T20:00:00`),
      currentTerm: true,
      currentWeekNumber: 2,
      weeks: [{ number: 2 }, { number: 3 }],
    })).toEqual(expect.objectContaining({ kind: 'tomorrow', course }));
  });

  test('reports Sunday complete only when Monday has no courses in this teaching week', () => {
    expect(mobileCourseSummary([
      { course_name: '下周课程', weekday: 1, weeks: [3], start_time: '08:30', end_time: '10:00' },
      { course_name: '今日已结束', weekday: 7, weeks: [2], start_time: '08:30', end_time: '10:00' },
    ], {
      now: new Date('2026-09-06T20:00:00'),
      currentTerm: true,
      currentWeekNumber: 2,
      weeks: [{ number: 2 }, { number: 3 }],
    })).toEqual(expect.objectContaining({ kind: 'complete' }));
  });

  test('shows tomorrow first course and advances the teaching week from Saturday to Sunday', () => {
    const courses = [
      { course_name: '明日早课', weekday: 7, weeks: [4], start_time: '08:30', end_time: '10:00' },
      { course_name: '明日晚课', weekday: 7, weeks: [4], start_time: '13:00', end_time: '14:30' },
    ];
    expect(mobileCourseSummary(courses, {
      now: new Date('2026-08-22T20:00:00'),
      currentTerm: true,
      currentWeekNumber: 3,
      weeks: [{ number: 3 }, { number: 4 }],
    })).toEqual(expect.objectContaining({
      kind: 'tomorrow',
      label: '明日',
      startTime: '08:30',
      course: expect.objectContaining({ course_name: '明日早课' }),
    }));
  });

  test('moves mobile days one at a time and crosses teaching-week boundaries', () => {
    const weeks = [{ number: 2 }, { number: 3 }, { number: 4 }];
    expect(adjacentMobileTimetableDay({ day: 1, week: 3, direction: 1, weeks }))
      .toEqual({ day: 2, week: 3 });
    expect(adjacentMobileTimetableDay({ day: 6, week: 3, direction: 1, weeks }))
      .toEqual({ day: 7, week: 4 });
    expect(adjacentMobileTimetableDay({ day: 7, week: 3, direction: -1, weeks }))
      .toEqual({ day: 6, week: 2 });
    expect(adjacentMobileTimetableDay({ day: 6, week: 4, direction: 1, weeks })).toBeNull();
  });

  test('moves the compact mobile timetable one teaching week at a time', () => {
    const weeks = [{ number: 2 }, { number: 3 }, { number: 4 }];
    expect(adjacentMobileTimetableWeek({ week: 3, direction: 1, weeks })).toBe(4);
    expect(adjacentMobileTimetableWeek({ week: 3, direction: -1, weeks })).toBe(2);
    expect(adjacentMobileTimetableWeek({ week: 4, direction: 1, weeks })).toBeNull();
  });

  test('compact mobile week view shows all weekdays and only course name and location', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const onCourseClick = jest.fn();
    const coursesByDay = Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]));
    coursesByDay[1] = [{
      id: 'monday-course',
      course_name: '周一课程',
      location: '信息楼101',
      teachers: ['教师甲'],
      course_nature: '必修',
      assessment_type: '考试',
      start_section: 1,
      end_section: 2,
      start_time: '08:30',
      end_time: '10:05',
    }];
    coursesByDay[3] = [{
      id: 'wednesday-course',
      course_name: '周三课程',
      location: '知行楼202',
      teachers: ['教师乙'],
      course_nature: '选修',
      start_section: 3,
      end_section: 4,
      start_time: '10:25',
      end_time: '12:00',
    }];
    try {
      await act(async () => {
        root.render(<MobileCompactWeekTimetable
          coursesByDay={coursesByDay}
          sections={[
            { number: 1, start_time: '08:30' },
            { number: 2, start_time: '09:25' },
            { number: 3, start_time: '10:30' },
            { number: 4, start_time: '11:25' },
          ]}
          selectedDay={1}
          onCourseClick={onCourseClick}
        />);
      });
      expect(container.querySelector('.timetable-grid.is-mobile-compact')).not.toBeNull();
      expect(container.querySelectorAll('.timetable-grid-header > div:not(.timetable-axis-heading)')).toHaveLength(7);
      expect(container.textContent).toContain('周一课程');
      expect(container.textContent).toContain('信息楼101');
      expect(container.textContent).toContain('周三课程');
      expect(container.textContent).toContain('知行楼202');
      expect(container.textContent).not.toContain('教师甲');
      expect(container.textContent).not.toContain('必修');
      expect(container.textContent).not.toContain('考试');
      await act(async () => container.querySelector('.timetable-course-block').click());
      expect(onCourseClick).toHaveBeenCalledWith(expect.objectContaining(coursesByDay[1][0]));
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('widens and vertically fits a multi-campus section axis without changing the day grid', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const sections = [{
      number: 1,
      name: '第1节',
      time_variants: [
        { key: 'nanhu', labels: ['南湖校区'], short_labels: ['南'], start_time: '08:00', end_time: '08:45' },
        { key: 'hunnan', labels: ['浑南校区'], short_labels: ['浑'], start_time: '08:30', end_time: '09:15' },
      ],
    }];
    expect(timetableSectionAxisMinimumHeight(sections[0], true)).toBeGreaterThanOrEqual(44);
    expect(timetableSectionAxisMinimumHeight(sections[0], false)).toBeGreaterThanOrEqual(58);
    try {
      await act(async () => {
        root.render(<TimetableGrid
          coursesByDay={Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]))}
          sections={sections}
          viewMode="week"
          mode="personal"
          currentTerm
          currentWeekNumber={1}
          showToday={false}
          onCourseClick={() => {}}
          presentation="mobile-compact"
        />);
      });
      const header = container.querySelector('.timetable-grid-header');
      expect(header.style.getPropertyValue('--timetable-mobile-axis-width')).toBe('68px');
      expect(Number.parseFloat(container.querySelector('.timetable-section-label').style.height))
        .toBeGreaterThanOrEqual(44);
      expect(container.querySelector('.timetable-section-time-variants').textContent)
        .toContain('南08:00');
      expect(container.querySelector('.timetable-section-time-variants').textContent)
        .toContain('浑08:30');
      expect(container.querySelectorAll('.timetable-day-column')).toHaveLength(7);
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('compact mobile timetable marks the course that is happening now', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.setSystemTime(new Date(2026, 8, 5, 10, 0, 0));
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const coursesByDay = Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]));
    coursesByDay[6] = [{
      id: 'current-compact-course',
      course_name: '正在上课的课程',
      location: '浑南校区信息楼A101',
      weekday: 6,
      weeks: [1],
      recurrence_unknown: false,
      start_section: 1,
      end_section: 2,
      start_time: '09:30',
      end_time: '10:30',
    }];
    try {
      await act(async () => {
        root.render(<MobileTimetable
          coursesByDay={coursesByDay}
          sections={[
            { number: 1, start_time: '08:30' },
            { number: 2, start_time: '09:25' },
          ]}
          selectedDay={6}
          viewMode="week"
          currentTerm
          currentWeekNumber={1}
          onDayChange={() => {}}
          onSwipeDay={() => {}}
          onSwipeWeek={() => {}}
          onCourseClick={() => {}}
          personalConflictMap={{}}
          compactWeekView
        />);
      });
      const currentCourse = container.querySelector('.timetable-course-block.is-course-now');
      expect(currentCourse).not.toBeNull();
      expect(currentCourse.textContent).toContain('正在上课的课程');
    } finally {
      await act(async () => root.unmount());
      container.remove();
      jest.useRealTimers();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('compact mobile week view expands a folded course before opening its details', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const onCourseClick = jest.fn();
    const coursesByDay = Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]));
    coursesByDay[1] = [
      {
        id: 'stack-first',
        course_name: '堆叠课程甲',
        location: 'A101',
        start_section: 1,
        end_section: 2,
      },
      {
        id: 'stack-second',
        course_name: '堆叠课程乙',
        location: '浑南校区信息学馆A区第一公共实验室B202',
        start_section: 1,
        end_section: 2,
      },
    ];
    try {
      await act(async () => {
        root.render(<MobileCompactWeekTimetable
          coursesByDay={coursesByDay}
          sections={[
            { number: 1, start_time: '08:00' },
            { number: 2, start_time: '08:55' },
          ]}
          selectedDay={1}
          onCourseClick={onCourseClick}
        />);
      });
      expect(container.querySelector('.timetable-course-cluster')).not.toBeNull();
      let courseBlocks = [...container.querySelectorAll('.timetable-cluster-stack-card')];
      expect(courseBlocks).toHaveLength(2);
      expect(courseBlocks[0].classList.contains('is-expanded')).toBe(true);
      expect(courseBlocks[1].classList.contains('is-folded')).toBe(true);

      await act(async () => courseBlocks[1].click());
      expect(onCourseClick).not.toHaveBeenCalled();
      courseBlocks = [...container.querySelectorAll('.timetable-cluster-stack-card')];
      expect(courseBlocks[0].classList.contains('is-folded')).toBe(true);
      expect(courseBlocks[1].classList.contains('is-expanded')).toBe(true);
      const expandedLocation = courseBlocks[1].querySelector('.timetable-cluster-stack-detail');
      expect(expandedLocation).not.toBeNull();
      expect(expandedLocation.textContent).toBe('浑南校区信息学馆A区第一公共实验室B202');
      expect(courseBlocks[1].textContent).toContain('浑南校区信息学馆A区第一公共实验室B202');

      const longLocationHeights = mobileCompactSectionHeights(
        [
          { number: 1, start_time: '08:00' },
          { number: 2, start_time: '08:55' },
        ],
        coursesByDay,
      );
      expect(longLocationHeights.reduce((total, height) => total + height, 0)).toBeGreaterThan(104);

      await act(async () => courseBlocks[1].click());
      expect(onCourseClick).toHaveBeenCalledTimes(1);
      expect(onCourseClick).toHaveBeenCalledWith(expect.objectContaining({ id: 'stack-second' }));
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('swiping the compact mobile week view changes weeks instead of days', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const onSwipeWeek = jest.fn();
    const onSwipeDay = jest.fn();
    try {
      await act(async () => {
        root.render(<MobileTimetable
          coursesByDay={Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]))}
          sections={[]}
          selectedDay={1}
          viewMode="week"
          currentTerm
          currentWeekNumber={3}
          onDayChange={() => {}}
          onSwipeDay={onSwipeDay}
          onSwipeWeek={onSwipeWeek}
          onCourseClick={() => {}}
          personalConflictMap={{}}
          compactWeekView
        />);
      });
      const timetable = container.querySelector('.timetable-mobile');
      await act(async () => {
        timetable.dispatchEvent(new MouseEvent('pointerdown', {
          bubbles: true, clientX: 240, clientY: 100,
        }));
        timetable.dispatchEvent(new MouseEvent('pointerup', {
          bubbles: true, clientX: 100, clientY: 104,
        }));
      });
      expect(onSwipeWeek).toHaveBeenCalledWith(1);
      expect(onSwipeDay).not.toHaveBeenCalled();
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('supports the compact seven-day grid in mobile term view without hidden week navigation', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const onSwipeWeek = jest.fn();
    const onSwipeDay = jest.fn();
    const coursesByDay = Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]));
    coursesByDay[2] = [{
      id: 'term-course',
      course_name: '全学期课程',
      location: '信息楼A101',
      weeks: [1, 3, 5],
      start_section: 1,
      end_section: 2,
      start_time: '08:30',
      end_time: '10:10',
    }];
    try {
      await act(async () => {
        root.render(<MobileTimetable
          coursesByDay={coursesByDay}
          sections={[]}
          selectedDay={2}
          viewMode="term"
          currentTerm
          currentWeekNumber={3}
          onDayChange={() => {}}
          onSwipeDay={onSwipeDay}
          onSwipeWeek={onSwipeWeek}
          onCourseClick={() => {}}
          personalConflictMap={{}}
          compactWeekView
        />);
      });
      expect(container.querySelector('[aria-label="本学期缩略课表"]')).not.toBeNull();
      expect(container.querySelector('.timetable-mobile-day-selector')).toBeNull();
      expect(container.querySelector('.timetable-mobile').classList.contains('is-term-view')).toBe(true);
      expect(container.textContent).toContain('全学期课程');
      const timetable = container.querySelector('.timetable-mobile');
      await act(async () => {
        timetable.dispatchEvent(new MouseEvent('pointerdown', {
          bubbles: true, clientX: 240, clientY: 100,
        }));
        timetable.dispatchEvent(new MouseEvent('pointerup', {
          bubbles: true, clientX: 100, clientY: 104,
        }));
      });
      expect(onSwipeWeek).not.toHaveBeenCalled();
      expect(onSwipeDay).not.toHaveBeenCalled();
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('keeps term view identifiable while preserving the focus anchor policy', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const props = {
      coursesByDay: Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []])),
      sections: Array.from({ length: 12 }, (_, index) => ({ number: index + 1 })),
      selectedDay: 1,
      currentTerm: true,
      currentWeekNumber: 1,
      onDayChange: () => {},
      onSwipeDay: () => {},
      onSwipeWeek: () => {},
      onCourseClick: () => {},
      personalConflictMap: {},
    };
    try {
      await act(async () => {
        root.render(<MobileTimetable {...props} viewMode="term" />);
      });
      expect(container.querySelector('.timetable-mobile').classList.contains('is-term-view')).toBe(true);
      expect(container.querySelector('.timetable-mobile-day-selector')).not.toBeNull();

      await act(async () => {
        root.render(<MobileTimetable {...props} viewMode="week" />);
      });
      expect(container.querySelector('.timetable-mobile').classList.contains('is-term-view')).toBe(false);

      const weekAnchor = document.createElement('div');
      const dayAnchor = document.createElement('div');
      expect(mobileInitialFocusAnchor('week', weekAnchor, dayAnchor)).toBe(weekAnchor);
      expect(mobileInitialFocusAnchor('term', weekAnchor, dayAnchor)).toBe(dayAnchor);
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('uses the shared seven-day grid for the mobile selection timetable', async () => {
    const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const onCourseClick = jest.fn();
    const onSlotSelect = jest.fn();
    const coursesByDay = Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]));
    coursesByDay[1] = [{
      id: 'candidate',
      course_name: '方案课程',
      location: '不应出现在紧凑选课网格',
      teachers: ['不应显示教师'],
      start_section: 1,
      end_section: 2,
      layer: 'candidate',
    }];
    try {
      await act(async () => {
        root.render(<MobileTimetable
          coursesByDay={coursesByDay}
          sections={[]}
          selectedDay={1}
          viewMode="week"
          currentTerm
          currentWeekNumber={1}
          onDayChange={() => {}}
          onSwipeDay={() => {}}
          onCourseClick={onCourseClick}
          personalConflictMap={{}}
          presentation="selection"
          onSlotSelect={onSlotSelect}
        />);
      });
      expect(container.querySelector('.timetable-grid.is-mobile-compact.is-selection-compact')).not.toBeNull();
      expect(container.querySelector('.timetable-mobile-day-selector')).toBeNull();
      expect(container.textContent).toContain('方案课程');
      expect(container.textContent).toContain('方案候选');
      expect(container.textContent).not.toContain('不应出现在紧凑选课网格');
      expect(container.textContent).not.toContain('不应显示教师');
      await act(async () => container.querySelector('.timetable-course-block').click());
      expect(onCourseClick).toHaveBeenCalledWith(expect.objectContaining({ id: 'candidate' }));
      await act(async () => container.querySelector('.timetable-slot-search').click());
      expect(onSlotSelect).toHaveBeenCalled();
    } finally {
      await act(async () => root.unmount());
      container.remove();
      global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
  });

  test('keeps simultaneous current courses countable and normalizes teacher names', () => {
    const now = new Date('2026-08-17T10:30:00');
    const summary = mobileCourseSummary([
      { course_name: '甲课', weekday: 1, weeks: [3], start_time: '10:00', end_time: '11:30' },
      { course_name: '乙课', weekday: 1, weeks: [3], start_time: '10:00', end_time: '11:30' },
    ], { now, currentTerm: true, currentWeekNumber: 3 });
    expect(summary.count).toBe(2);
    expect(courseTeacherText({ teachers: ['教师甲', '教师甲'], teacher: '教师乙' })).toBe('教师甲、教师乙');
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
