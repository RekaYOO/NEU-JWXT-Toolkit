import {
  academicGapCatalogScope,
  applyCatalogDisplayLayout,
  catalogAvailabilityRequestMode,
  catalogAvailabilityRemoteFilters,
  changedOfficialBatchTimes,
  catalogGroupLiveStats,
  catalogGroupsForDisplay,
  courseCampusLabels,
  createCatalogDisplayLayout,
  extendCatalogDisplayLayout,
  filterAcademicPlanGapsForBatch,
  findExactSelectionClassRecord,
  findMatchingSelectionRecord,
  inferBatchRequirementType,
  immediateSelectionConflictMap,
  isCrossCampusCourse,
  isGeneralElectiveCategory,
  isCurrentBatchSelectionRecord,
  academicPlanSelectionRecords,
  matchAcademicGapCatalogFilters,
  mergeSelectionConflictMatches,
  mergeCatalogRefreshPreservingOrder,
  mergeCatalogFilterLayers,
  matchesArchivedCourseFilters,
  matchesCatalogAvailability,
  sameSelectionCourse,
  patchCatalogSelection,
  removeCourseFromSelectionConflictMap,
  removeSelectionRecord,
  selectionParticipantCount,
  selectionParticipantLabel,
  selectionTimeConflictStatus,
  sortCatalogGroupsBySelectability,
  summarizeSelectionConflictsByClass,
  uniqueDisplayLabels,
  toggleCatalogPreviewCourse,
  unplannedCurrentWeightSelections,
  reconcileUngroupedWeightPlan,
  UNGROUPED_WEIGHT_GROUP_ID,
  upsertSelectionRecord,
} from './jwxkSchedule';

test('conflict matches merge duplicate sources but keep distinct split-course times', () => {
  const merged = mergeSelectionConflictMatches([
    { status: 'conflict', baseline_meeting_id: 'jwxt-1', baseline_course_code: 'A1', baseline_course_name: '企业战略管理', baseline_weeks: [5, 6], overlapping_weeks: [5, 6], weekday: 2, start_section: 9, end_section: 10 },
    { status: 'conflict', baseline_meeting_id: 'jwxk-1', baseline_course_code: 'A1', baseline_course_name: '企业战略管理', baseline_weeks: [7, 8], overlapping_weeks: [7, 8], weekday: 2, start_section: 9, end_section: 10 },
    { status: 'conflict', baseline_meeting_id: 'jwxt-2', baseline_course_code: 'A1', baseline_course_name: '企业战略管理', baseline_weeks: [1, 2], overlapping_weeks: [1, 2], weekday: 5, start_section: 7, end_section: 8 },
  ]);
  expect(merged).toHaveLength(2);
  expect(merged[0]).toMatchObject({
    baseline_weeks: [5, 6, 7, 8], overlapping_weeks: [5, 6, 7, 8],
    weekday: 2, start_section: 9, end_section: 10,
  });
  expect(merged[1]).toMatchObject({ weekday: 5, start_section: 7, end_section: 8 });
});

test('conflict matches merge the same named course when one source lacks its code', () => {
  const merged = mergeSelectionConflictMatches([{
    status: 'conflict', baseline_course_name: '习近平经济思想概论',
    baseline_course_code: 'A1', baseline_teaching_class_id: 'class-a',
    baseline_weeks: [9, 10], overlapping_weeks: [9, 10],
    weekday: 2, start_section: 7, end_section: 8,
  }, {
    status: 'conflict', baseline_course_name: '习近平经济思想概论',
    baseline_course_code: '', baseline_teaching_class_id: 'class-b',
    baseline_weeks: [11, 12], overlapping_weeks: [11, 12],
    weekday: 2, start_section: 7, end_section: 8,
  }]);
  expect(merged).toHaveLength(1);
  expect(merged[0]).toMatchObject({
    baseline_course_code: 'A1', baseline_weeks: [9, 10, 11, 12],
    overlapping_weeks: [9, 10, 11, 12],
  });
});

test('successful withdrawal removes stale conflict matches for that course only', () => {
  const cleaned = removeCourseFromSelectionConflictMap({
    candidate: {
      status: 'conflict',
      matches: [{
        status: 'conflict', baseline_course_code: 'A1', baseline_course_name: '已退课程',
        overlapping_weeks: [1], weekday: 1, start_section: 1, end_section: 2,
      }, {
        status: 'conflict', baseline_course_code: 'B2', baseline_course_name: '仍冲突课程',
        overlapping_weeks: [2], weekday: 2, start_section: 3, end_section: 4,
      }],
    },
  }, { course_code: 'A1', course_name: '已退课程' });
  expect(cleaned.candidate.status).toBe('conflict');
  expect(cleaned.candidate.matches).toHaveLength(1);
  expect(cleaned.candidate.matches[0].baseline_course_code).toBe('B2');

  const cleared = removeCourseFromSelectionConflictMap(cleaned, {
    course_code: 'B2', course_name: '仍冲突课程',
  });
  expect(cleared.candidate).toEqual(expect.objectContaining({ status: 'clear', matches: [] }));
});

test('participant metric follows grab and weight round semantics', () => {
  const course = { selected_count: 40, weight_participant_count: 63, capacity: 50 };
  expect(selectionParticipantCount(course, '02')).toBe(40);
  expect(selectionParticipantLabel(course, '02')).toBe('已选人数');
  expect(selectionParticipantCount(course, '04')).toBe(63);
  expect(selectionParticipantLabel(course, '04')).toBe('已投注人数');
  expect(matchesCatalogAvailability({ ...course, selection_type_code: '04' }, 'available')).toBe(false);
});

test('destructive selection actions never fall back to another class of the same course', () => {
  const records = [{ class_id: 'weighted-a', course_code: 'COURSE-1', devoted_weight: 10 }];
  expect(findMatchingSelectionRecord(records, {
    class_id: 'alternative-b', course_code: 'COURSE-1',
  })).toBe(records[0]);
  expect(findExactSelectionClassRecord(records, {
    class_id: 'alternative-b', course_code: 'COURSE-1',
  })).toBeNull();
  expect(findExactSelectionClassRecord(records, {
    class_id: 'weighted-a', course_code: 'COURSE-1',
  })).toBe(records[0]);
});

test('archive campus labels normalize stored codes and schedule campus names', () => {
  expect(courseCampusLabels({
    campus: '01',
    schedules: [{ campus_name: '浑南校区' }, { campus: '00' }],
  })).toEqual(['浑南校区', '南湖校区']);
});

test('archived course filters combine taxonomy, campus and one matching meeting locally', () => {
  const course = {
    campus: '01',
    course_nature: '选修',
    course_category: '通识选修课',
    course_categories: ['通识选修'],
    general_elective_category: '人文社会科学类',
    department: '马克思主义学院',
    schedules: [
      { weekday: 2, start_section: 3, end_section: 4 },
      { weekday: 5, start_section: 9, end_section: 10 },
    ],
  };
  expect(matchesArchivedCourseFilters(course, {
    campus: '浑南校区', courseNature: '选修', courseCategory: '通识选修',
    generalElectiveCategory: '人文社会科学类', department: '马克思主义学院',
    weekday: '2', startSection: '3', endSection: '4',
  })).toBe(true);
  expect(matchesArchivedCourseFilters(course, {
    weekday: '2', startSection: '9', endSection: '10',
  })).toBe(false);
});

test('manual batch refresh identifies official start or end time changes', () => {
  expect(changedOfficialBatchTimes([{
    code: 'round-1', name: '轮次1',
    begin_time: '2026-08-16 13:00:00', end_time: '2026-08-17 00:00:00',
  }], [{
    code: 'round-1', name: '轮次1',
    begin_time: '2026-08-16T14:00:00', end_time: '2026-08-17 01:00:00',
  }])).toEqual([expect.objectContaining({
    batch_code: 'round-1', old_start_at: '2026-08-16 13:00:00',
    start_at: '2026-08-16T14:00:00', end_at: '2026-08-17 01:00:00',
  })]);
  expect(changedOfficialBatchTimes([], [{
    code: 'round-1', begin_time: '2026-08-16 13:00:00', end_time: '2026-08-17 00:00:00',
  }])).toEqual([]);
});

test('manual current-round weight selections can be identified for plan import', () => {
  const courses = [
    { class_id: 'A1', selection_record_type: 'volunteered', weight_participant_count: 12, capacity: 30 },
    { class_id: 'A2', selection_record_type: 'volunteered', weight_participant_count: 0, capacity: 0 },
    { class_id: 'A3', selection_record_type: 'selected', weight_participant_count: 8, capacity: 30 },
    { class_id: 'A4', selection_record_type: 'volunteered', weight_participant_count: 9, capacity: 30 },
  ];
  expect(unplannedCurrentWeightSelections(courses, [{ class_id: 'A4' }], '04')
    .map(course => course.class_id)).toEqual(['A1']);
  expect(unplannedCurrentWeightSelections(courses, [], '02')).toEqual([]);
});

test('course source labels are deduplicated after user-facing translation', () => {
  const labels = uniqueDisplayLabels(
    ['TJKC', '任务推荐班课程', 'FANKC'],
    value => ({ TJKC: '任务推荐班课程', FANKC: '培养方案内课' }[value] || value),
  );
  expect(labels).toEqual(['任务推荐班课程', '培养方案内课']);
});

test('catalog group summary only exposes live conflict-free and capacity counts', () => {
  const group = {
    classes: [
      { class_id: 'clear', capacity: 50, weight_participant_count: 20, conflict: false },
      { class_id: 'local-conflict', capacity: 30, weight_participant_count: 30, conflict: false },
      { class_id: 'official-conflict', capacity: 40, weight_participant_count: 10, conflict: true },
    ],
  };
  expect(catalogGroupLiveStats(group, {
    clear: { status: 'clear' },
    'local-conflict': {
      status: 'conflict',
      matches: [{
        status: 'conflict', source: 'personal_timetable', overlapping_weeks: [1],
        weekday: 1, start_section: 1, end_section: 2,
      }],
    },
    'official-conflict': { status: 'clear' },
  }, '04')).toEqual({
    conflict_free_count: 2,
    all_classes_conflict: false,
    available_count: 2,
  });
});

test('official conflict flag is not misreported as a local time conflict', () => {
  const stats = catalogGroupLiveStats({
    classes: [{ class_id: 'cross-campus', conflict: true, capacity: 20, selected_count: 5 }],
  }, { 'cross-campus': { status: 'clear' } }, '02');
  expect(stats).toMatchObject({ conflict_free_count: 1, all_classes_conflict: false });
});

test('red time-conflict state requires a concrete non-official overlap', () => {
  expect(selectionTimeConflictStatus({
    status: 'conflict',
    matches: [{
      status: 'conflict', source: 'jwxk_official', overlapping_weeks: [1],
      weekday: 1, start_section: 1, end_section: 2,
    }],
  })).toBe('unknown');
  expect(selectionTimeConflictStatus({
    status: 'conflict',
    matches: [{
      status: 'conflict', source: 'personal_timetable', overlapping_weeks: [1, 2],
      weekday: 1, start_section: 1, end_section: 2,
    }],
  })).toBe('conflict');
});

test('cross-campus detection compares normalized student and teaching campuses', () => {
  expect(isCrossCampusCourse({ campus: '00', campus_name: '南湖校区' }, '01', '浑南校区')).toBe(true);
  expect(isCrossCampusCourse({ schedules: [{ campus_name: '浑南校区' }] }, '01')).toBe(false);
  expect(isCrossCampusCourse({ schedules: [] }, '01')).toBe(false);
});

test('general elective subcategory is enabled for every label containing 通识选修', () => {
  expect(isGeneralElectiveCategory('通识选修')).toBe(true);
  expect(isGeneralElectiveCategory('本科通识选修类')).toBe(true);
  expect(isGeneralElectiveCategory('专业方向类')).toBe(false);
});

test('catalog display coalesces duplicate groups with the same course code', () => {
  const groups = catalogGroupsForDisplay([
    { group_id: 'old-a', course_code: 'C1', course_name: '课程', classes: [{ class_id: 'A' }] },
    { group_id: 'old-b', course_code: 'C1', course_name: '课程', classes: [{ class_id: 'B' }] },
  ]);
  expect(groups).toHaveLength(1);
  expect(groups[0].classes.map(item => item.class_id)).toEqual(['A', 'B']);
  const layout = createCatalogDisplayLayout(groups);
  expect(applyCatalogDisplayLayout([
    { group_id: 'old-a', course_code: 'C1', classes: [{ class_id: 'A' }] },
    { group_id: 'old-b', course_code: 'C1', classes: [{ class_id: 'B' }] },
  ], layout)[0].classes.map(item => item.class_id)).toEqual(['A', 'B']);
});

test('catalog group is marked conflicting only when every teaching class conflicts', () => {
  expect(catalogGroupLiveStats({
    classes: [
      { class_id: 'official', conflict: true },
      { class_id: 'local', conflict: false },
    ],
  }, {
    official: {
      status: 'conflict',
      matches: [{
        status: 'conflict', source: 'selection_plan', overlapping_weeks: [2],
        weekday: 2, start_section: 3, end_section: 4,
      }],
    },
    local: {
      status: 'conflict',
      matches: [{
        status: 'conflict', source: 'personal_timetable', overlapping_weeks: [1],
        weekday: 1, start_section: 1, end_section: 2,
      }],
    },
  }).all_classes_conflict).toBe(true);

  expect(catalogGroupLiveStats({
    classes: [
      { class_id: 'conflict', conflict: true },
      { class_id: 'unknown', conflict: false },
    ],
  }, {
    unknown: { status: 'unknown' },
  }).all_classes_conflict).toBe(false);
});

test('catalog preview toggles only the explicitly selected teaching class', () => {
  const first = { class_id: 'first', course_name: '第一门' };
  const second = { class_id: 'second', course_name: '第二门' };
  const one = toggleCatalogPreviewCourse([], first);
  expect(toggleCatalogPreviewCourse(one, second).map(item => item.class_id)).toEqual(['first', 'second']);
  expect(toggleCatalogPreviewCourse(one, first)).toEqual([]);
});

test('weight records with zero participants and zero capacity belong to another batch', () => {
  expect(isCurrentBatchSelectionRecord({
    weight_participant_count: 0, capacity: 0,
  }, '04')).toBe(false);
  expect(isCurrentBatchSelectionRecord({
    weight_participant_count: 0, capacity: 30,
  }, '04')).toBe(true);
  expect(isCurrentBatchSelectionRecord({
    selected_count: 0, capacity: 0,
  }, '02')).toBe(true);
});

test('current manual weights are reconciled into the reserved ungrouped plan', () => {
  const courses = [{
    class_id: 'A1', course_code: 'C1', course_name: '课程一', course_category: '专业方向类',
    selection_record_type: 'volunteered', weight_participant_count: 12, capacity: 30,
    devoted_weight: 15,
  }, {
    class_id: 'A2', course_code: 'C2', course_name: '课程二',
    selection_record_type: 'volunteered', weight_participant_count: 8, capacity: 20,
  }];
  const plan = [{ class_id: 'A2', plan_group_id: 'user-group', course_name: '课程二' }];
  const groups = [{ group_id: 'user-group', name: '目标组', target_count: 1 }];

  const result = reconcileUngroupedWeightPlan(courses, plan, groups, '04');
  expect(result.changed).toBe(true);
  expect(result.groups).toEqual([
    groups[0],
    expect.objectContaining({ group_id: UNGROUPED_WEIGHT_GROUP_ID, name: '未分组', target_count: 1 }),
  ]);
  expect(result.items).toHaveLength(2);
  expect(result.items.find(item => item.class_id === 'A1')).toMatchObject({
    plan_group_id: UNGROUPED_WEIGHT_GROUP_ID,
    course_category: '专业方向类',
    devoted_weight: 15,
    selection_record_type: 'volunteered',
  });
});

test('ungrouped reconciliation removes withdrawn records but keeps ordinary candidates', () => {
  const ordinary = { class_id: 'A1', plan_group_id: 'user-group', course_name: '普通候选' };
  const stale = {
    class_id: 'A2', plan_group_id: UNGROUPED_WEIGHT_GROUP_ID,
    plan_group_name: '未分组', plan_group_target_count: 1,
  };
  const result = reconcileUngroupedWeightPlan([], [ordinary, stale], [{
    group_id: 'user-group', name: '目标组', target_count: 1,
  }, {
    group_id: UNGROUPED_WEIGHT_GROUP_ID, name: '未分组', target_count: 1,
  }], '04');

  expect(result.items).toEqual([ordinary]);
  expect(result.groups).toEqual([{ group_id: 'user-group', name: '目标组', target_count: 1 }]);
  expect(reconcileUngroupedWeightPlan([], result.items, result.groups, '04')).toEqual({
    items: result.items, groups: result.groups, changed: false,
  });
});

test('ungrouped reconciliation fills schedule and category when the archive becomes ready', () => {
  const existing = {
    class_id: 'A1', course_name: '课程一', plan_group_id: UNGROUPED_WEIGHT_GROUP_ID,
    plan_group_name: '未分组', plan_group_target_count: 1, priority: 1,
  };
  const current = {
    class_id: 'A1', course_name: '课程一', selection_record_type: 'volunteered',
    weight_participant_count: 5, capacity: 20, devoted_weight: 30,
    normalized_course_category: '专业方向类',
    schedules: [{ meeting_id: 'm1', weekday: 1, start_section: 1, end_section: 2 }],
  };
  const group = { group_id: UNGROUPED_WEIGHT_GROUP_ID, name: '未分组', target_count: 1 };

  const result = reconcileUngroupedWeightPlan([current], [existing], [group], '04');
  expect(result.changed).toBe(true);
  expect(result.items[0]).toMatchObject({
    normalized_course_category: '专业方向类', devoted_weight: 30,
    schedules: [expect.objectContaining({ meeting_id: 'm1' })],
  });
  expect(reconcileUngroupedWeightPlan([current], result.items, result.groups, '04').changed)
    .toBe(false);
});

test('moving ungrouped weights into different groups is stable on the next reconciliation', () => {
  const courses = ['A1', 'A2'].map((classId, index) => ({
    class_id: classId, course_code: `C${index + 1}`,
    selection_record_type: 'volunteered', weight_participant_count: 5, capacity: 20,
  }));
  const moved = courses.map((course, index) => ({
    ...course, plan_group_id: `group-${index + 1}`, priority: 1,
  }));
  const groups = [{ group_id: 'group-1', name: '第一组', target_count: 1 }, {
    group_id: 'group-2', name: '第二组', target_count: 1,
  }];

  expect(reconcileUngroupedWeightPlan(courses, moved, groups, '04')).toEqual({
    items: moved, groups, changed: false,
  });
});

test('other-round zero-capacity weight records do not enter the ungrouped plan', () => {
  const items = [{
    class_id: 'OLD', selection_record_type: 'volunteered',
    weight_participant_count: 0, capacity: 0,
  }];
  expect(reconcileUngroupedWeightPlan(items, [], [], '04')).toEqual({
    items: [], groups: [], changed: false,
  });
});

test('academic-plan projection keeps current volunteered courses and drops other-round records', () => {
  const records = academicPlanSelectionRecords([{
    course_code: 'CURRENT', course_name: '本轮已投课程',
    selection_record_type: 'volunteered', weight_participant_count: 12, capacity: 30,
  }, {
    course_code: 'OTHER', course_name: '其他轮次课程',
    selection_record_type: 'volunteered', weight_participant_count: 0, capacity: 0,
  }, {
    course_code: 'CURRENT', course_name: '本轮已投课程重复记录',
    selection_record_type: 'volunteered', weight_participant_count: 12, capacity: 30,
  }], '04');

  expect(records).toHaveLength(1);
  expect(records[0].course_code).toBe('CURRENT');
});

test('batch course nature is inferred from the batch name before its notice', () => {
  expect(inferBatchRequirementType({
    name: '轮次1 必修课初选（不含体育课）', notice: '课程范围包含必修课程',
  })).toBe('required');
  expect(inferBatchRequirementType({ name: '轮次3 选修课初选' })).toBe('elective');
  expect(inferBatchRequirementType({ name: '体育课选课', notice: '未说明课程性质' })).toBe('');
});

test('academic-plan gaps hide the opposite nature for a known batch type', () => {
  const gaps = [
    { name: '必修缺口', requirement_type: 'required' },
    { name: '选修缺口', requirement_type: 'elective' },
    { name: '综合缺口', requirement_type: 'mixed' },
  ];
  expect(filterAcademicPlanGapsForBatch(gaps, { name: '轮次3 选修课初选' }))
    .toEqual([gaps[1], gaps[2]]);
  expect(filterAcademicPlanGapsForBatch(gaps, { name: '轮次1 必修课初选' }))
    .toEqual([gaps[0], gaps[2]]);
  expect(filterAcademicPlanGapsForBatch(gaps, { name: '其他轮次' })).toEqual(gaps);
});

test('manual catalog filters layer over rather than erase plan-gap filters', () => {
  expect(mergeCatalogFilterLayers(
    { campus: '01', courseCategory: '' },
    { campus: '', courseCategory: '专业方向类' },
    ['campus', 'courseCategory'],
  )).toEqual({ campus: '01', courseCategory: '专业方向类' });
  expect(mergeCatalogFilterLayers(
    { courseCategory: '专业基础课' },
    { courseCategory: '专业方向类' },
    ['courseCategory'],
  )).toEqual({ courseCategory: '专业基础课' });
});

const meeting = overrides => ({
  meeting_id: 'meeting', course_code: 'A', course_name: '课程A',
  weeks: [1, 2], weekday: 1, start_section: 1, end_section: 2,
  ...overrides,
});

test('same course is ignored by code and normalized name fallback', () => {
  expect(sameSelectionCourse(meeting({}), meeting({ meeting_id: 'other' }))).toBe(true);
  expect(sameSelectionCourse(
    meeting({ course_code: '', course_name: '课程 A' }),
    meeting({ course_code: '', course_name: '课程A' }),
  )).toBe(true);
});

test('manual mutations patch only the affected selected record and course group', () => {
  const original = [{ class_id: 'A-1', course_code: 'A', course_name: '课程A' }, {
    class_id: 'B-1', course_code: 'B', course_name: '课程B',
  }];
  const updated = upsertSelectionRecord(original, {
    class_id: 'A-1', course_code: 'A', course_name: '课程A', devoted_weight: 20,
  });
  expect(updated).toHaveLength(2);
  expect(updated.find(item => item.class_id === 'A-1').devoted_weight).toBe(20);
  expect(removeSelectionRecord(updated, { class_id: 'A-1', course_code: 'A' })).toEqual([
    expect.objectContaining({ class_id: 'B-1' }),
  ]);

  const groups = patchCatalogSelection([{
    group_id: 'A', course_code: 'A', classes: [
      { class_id: 'A-1', course_code: 'A' },
      { class_id: 'A-2', course_code: 'A' },
    ],
  }, {
    group_id: 'B', course_code: 'B', classes: [{ class_id: 'B-1', course_code: 'B' }],
  }], { class_id: 'A-1', course_code: 'A' }, { selected: true, devotedWeight: 20 });
  expect(groups[0].classes).toEqual([
    expect.objectContaining({ class_id: 'A-1', selected: true, course_already_selected: true, devoted_weight: 20 }),
    expect.objectContaining({ class_id: 'A-2', selected: false, course_already_selected: true }),
  ]);
  expect(groups[1].classes[0].course_already_selected).toBeUndefined();
});

test('selection mutation is only confirmed by the official selected record', () => {
  const records = [
    { class_id: 'A113494', course_code: 'A1442000170', course_name: '物流与供应链管理' },
  ];
  expect(findMatchingSelectionRecord(records, {
    class_id: 'A113494', course_code: 'A1442000170', course_name: '物流与供应链管理',
  })).toBe(records[0]);
  expect(findMatchingSelectionRecord(records, {
    class_id: 'A113493', course_code: 'A1442000170', course_name: '物流与供应链管理',
  })).toBe(records[0]);
  expect(findMatchingSelectionRecord(records, {
    class_id: 'OTHER', course_code: 'A0000000000', course_name: '另一门课程',
  })).toBeNull();
});

test('immediate conflicts require matching term schedule dimensions represented by the view', () => {
  const candidate = meeting({ meeting_id: 'candidate', course_code: 'B', course_name: '课程B', weeks: [2, 3], start_section: 2, end_section: 4 });
  const result = immediateSelectionConflictMap([meeting({ meeting_id: 'mine' })], [candidate]);
  expect(result.candidate.status).toBe('conflict');
  expect(result.candidate.matches[0]).toMatchObject({
    baseline_course_name: '课程A', overlapping_weeks: [2], start_section: 2, end_section: 2,
  });
});

test('two volunteered courses outside plan groups receive symmetric conflict results', () => {
  const first = meeting({
    meeting_id: 'weighted-a', course_code: 'A', course_name: '现代艺术',
    layer: 'pending', weeks: [1, 2, 3], start_section: 5, end_section: 6,
  });
  const second = meeting({
    meeting_id: 'weighted-b', course_code: 'B', course_name: '机械制造技术基础',
    layer: 'pending', weeks: [2, 3, 4], start_section: 5, end_section: 6,
  });
  const result = immediateSelectionConflictMap([first, second], [first, second]);
  expect(result['weighted-a']).toMatchObject({ status: 'conflict' });
  expect(result['weighted-b']).toMatchObject({ status: 'conflict' });
  expect(result['weighted-a'].matches[0]).toMatchObject({
    baseline_course_name: '机械制造技术基础',
    overlapping_weeks: [2, 3],
    source: 'selection_candidate_local',
  });
  expect(result['weighted-b'].matches[0]).toMatchObject({
    baseline_course_name: '现代艺术',
    overlapping_weeks: [2, 3],
    source: 'selection_candidate_local',
  });
});

test('different weeks are clear while incomplete candidates remain unknown', () => {
  const different = meeting({ meeting_id: 'different', course_code: 'B', weeks: [3] });
  const unknown = meeting({ meeting_id: 'unknown', course_code: 'C', weeks: [] });
  const result = immediateSelectionConflictMap([meeting({})], [different, unknown]);
  expect(result.different.status).toBe('clear');
  expect(result.unknown.status).toBe('unknown');
});

test('meeting conflicts are summarized for every class without requiring hover', () => {
  const courses = [{
    class_id: 'class-a',
    meetings: [meeting({ meeting_id: 'a-1' }), meeting({ meeting_id: 'a-2' })],
  }, {
    class_id: 'class-b',
    meetings: [meeting({ meeting_id: 'b-1' })],
  }, {
    class_id: 'class-c',
    meetings: [],
  }];
  const result = summarizeSelectionConflictsByClass(courses, {
    'a-1': { status: 'clear', matches: [] },
    'a-2': { status: 'conflict', matches: [{ baseline_meeting_id: 'mine', source: 'personal' }] },
    'b-1': { status: 'clear', matches: [] },
  });
  expect(result['class-a']).toMatchObject({ status: 'conflict' });
  expect(result['class-b']).toEqual({ status: 'clear', matches: [] });
  expect(result['class-c']).toEqual({ status: 'unknown', matches: [] });
  expect(summarizeSelectionConflictsByClass(courses, {}, { baselineReady: false })['class-b'].status).toBe('unknown');
});

test('selectable and available are independent catalog dimensions', () => {
  const fullButSelectable = {
    eligibility_status: 'selectable', capacity: 30, selected_count: 30,
    full: true, restricted: false,
  };
  expect(matchesCatalogAvailability(fullButSelectable, 'selectable')).toBe(true);
  expect(matchesCatalogAvailability(fullButSelectable, 'available')).toBe(false);
  expect(catalogAvailabilityRemoteFilters('selectable')).toEqual({});
  expect(catalogAvailabilityRemoteFilters('available')).toEqual({ SFYM: '0' });
  expect(catalogAvailabilityRequestMode('selectable')).toBe('all');
  expect(catalogAvailabilityRequestMode('available')).toBe('available');
});

test('academic-plan gap searches the complete catalog when ALLKC is available', () => {
  expect(academicGapCatalogScope([
    { code: 'ALL' }, { code: 'ROUND' }, { code: 'FANKC' }, { code: 'ALLKC' },
  ])).toBe('ALL');
  expect(academicGapCatalogScope([
    { code: 'ROUND' }, { code: 'FANKC' }, { code: 'ALLKC' },
  ])).toBe('ALLKC');
  expect(academicGapCatalogScope([{ code: 'ROUND' }, { code: 'FANKC' }])).toBe('ROUND');
});

test('catalog keeps unavailable courses but places them after selectable courses', () => {
  const sorted = sortCatalogGroupsBySelectability([{
    group_id: 'unavailable',
    classes: [{ class_id: 'u', eligibility_status: 'unavailable', full: false }],
  }, {
    group_id: 'mixed',
    classes: [
      { class_id: 'full', eligibility_status: 'selectable', full: true, selected_count: 30 },
      { class_id: 'open', eligibility_status: 'selectable', full: false, selected_count: 20 },
    ],
  }, {
    group_id: 'pending',
    classes: [{ class_id: 'p', eligibility_status: 'unknown', full: false }],
  }]);
  expect(sorted.map(group => group.group_id)).toEqual(['mixed', 'pending', 'unavailable']);
  expect(sorted[0].classes.map(course => course.class_id)).toEqual(['open', 'full']);
});

test('catalog places task-recommended courses first within the same availability state', () => {
  const sorted = sortCatalogGroupsBySelectability([{
    group_id: 'ordinary',
    classes: [{ class_id: 'ordinary-class', eligibility_status: 'selectable', source_scopes: ['ALLKC'] }],
  }, {
    group_id: 'recommended-by-scope',
    classes: [{ class_id: 'recommended-class', eligibility_status: 'selectable', source_scopes: ['TJKC', 'ALLKC'] }],
  }, {
    group_id: 'recommended-by-tag',
    source_tags: ['任务推荐班课程', '全校课程查询'],
    classes: [{ class_id: 'tagged-class', eligibility_status: 'selectable', source_scopes: ['ALLKC'] }],
  }, {
    group_id: 'recommended-but-unavailable',
    source_tags: ['任务推荐班课程'],
    classes: [{ class_id: 'unavailable-class', eligibility_status: 'unavailable', source_scopes: ['TJKC'] }],
  }]);

  expect(sorted.map(group => group.group_id)).toEqual([
    'recommended-by-scope', 'recommended-by-tag', 'ordinary', 'recommended-but-unavailable',
  ]);
});

test('background eligibility and capacity updates preserve the loaded catalog layout', () => {
  const initiallyVisible = catalogGroupsForDisplay([{
    group_id: 'first',
    classes: [
      { class_id: 'first-a', eligibility_status: 'selectable', selected_count: 8, schedules: [] },
      { class_id: 'first-b', eligibility_status: 'selectable', selected_count: 9, schedules: [] },
    ],
  }, {
    group_id: 'second',
    classes: [{ class_id: 'second-a', eligibility_status: 'selectable', selected_count: 10, schedules: [] }],
  }], { availability: 'selectable' });
  const layout = createCatalogDisplayLayout(initiallyVisible);

  const refreshed = mergeCatalogRefreshPreservingOrder(initiallyVisible, [{
    group_id: 'second',
    classes: [{ class_id: 'second-a', eligibility_status: 'selectable', selected_count: 2, schedules: [] }],
  }, {
    group_id: 'first',
    classes: [
      { class_id: 'first-b', eligibility_status: 'selectable', selected_count: 1, schedules: [] },
      { class_id: 'first-a', eligibility_status: 'unavailable', selected_count: 30, schedules: [] },
    ],
  }]);
  const visible = applyCatalogDisplayLayout(
    refreshed,
    extendCatalogDisplayLayout(layout, catalogGroupsForDisplay(refreshed, { availability: 'selectable' })),
  );

  expect(visible.map(group => group.group_id)).toEqual(['first', 'second']);
  expect(visible[0].classes.map(course => course.class_id)).toEqual(['first-a', 'first-b']);
  expect(visible[0].classes[0]).toMatchObject({ eligibility_status: 'unavailable', selected_count: 30 });
});

test('academic-plan gaps map to official task category and nature filters', () => {
  expect(matchAcademicGapCatalogFilters({
    name: '专业基础类',
    originalName: '专业基础类',
    path_array: ['专业教育', '专业基础类', '必修'],
    requirement_type: 'required',
    course_natures: ['必修'],
  }, {
    course_categories: [{ value: '专业基础课', label: '专业基础课' }],
    course_natures: [{ value: '必修', label: '必修' }, { value: '选修', label: '选修' }],
  })).toEqual({
    courseCategory: '专业基础课', courseNature: '必修',
    generalElectiveCategory: '', gapCategoryMatched: true,
  });

  expect(matchAcademicGapCatalogFilters({
    name: '人文社会科学类', requirement_type: 'elective', course_natures: ['选修'],
  }, {
    course_categories: ['人文社会科学类'], course_natures: ['选修'],
  })).toEqual({
    courseCategory: '人文社会科学类', courseNature: '选修',
    generalElectiveCategory: '', gapCategoryMatched: true,
  });

  expect(matchAcademicGapCatalogFilters({
    name: '人文社会科学类', path_array: ['通识类', '人文社会科学类'],
    requirement_type: 'elective', course_natures: ['选修'],
  }, {
    course_categories: ['通识选修类', '通识选修课'], course_natures: ['选修'],
    general_elective_categories: ['科学素养类', '人文社会科学类'],
  })).toEqual({
    courseCategory: '通识选修', courseNature: '选修',
    generalElectiveCategory: '人文社会科学类',
    gapCategoryMatched: true,
  });

  expect(matchAcademicGapCatalogFilters({
    name: '科学素养类', requirement_type: 'elective', course_natures: ['选修'],
  }, {
    course_categories: ['通识选修课'], course_natures: ['选修'],
    general_elective_categories: ['科学素养类'],
  })).toEqual({
    courseCategory: '通识选修', courseNature: '选修',
    generalElectiveCategory: '科学素养类',
    gapCategoryMatched: true,
  });
});

test('academic-plan gap reports when only a broader fallback exists in the round', () => {
  expect(matchAcademicGapCatalogFilters({
    name: '人文社会科学类', path_array: ['通识类', '人文社会科学类'],
    requirement_type: 'elective', course_natures: ['选修'],
  }, {
    course_categories: ['通识选修课'], course_natures: ['选修'],
    general_elective_categories: ['科学素养类'],
  })).toEqual({
    courseCategory: '通识选修', courseNature: '选修',
    generalElectiveCategory: '', gapCategoryMatched: false,
  });
});
