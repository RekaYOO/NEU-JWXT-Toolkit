import {
  academicGapCatalogScope,
  catalogAvailabilityRequestMode,
  catalogAvailabilityRemoteFilters,
  filterAcademicPlanGapsForBatch,
  findMatchingSelectionRecord,
  inferBatchRequirementType,
  immediateSelectionConflictMap,
  isCurrentBatchSelectionRecord,
  matchAcademicGapCatalogFilters,
  mergeCatalogFilterLayers,
  matchesCatalogAvailability,
  sameSelectionCourse,
  selectionParticipantCount,
  selectionParticipantLabel,
  sortCatalogGroupsBySelectability,
  summarizeSelectionConflictsByClass,
} from './jwxkSchedule';

test('participant metric follows grab and weight round semantics', () => {
  const course = { selected_count: 40, weight_participant_count: 63, capacity: 50 };
  expect(selectionParticipantCount(course, '02')).toBe(40);
  expect(selectionParticipantLabel(course, '02')).toBe('已选人数');
  expect(selectionParticipantCount(course, '04')).toBe(63);
  expect(selectionParticipantLabel(course, '04')).toBe('已投注人数');
  expect(matchesCatalogAvailability({ ...course, selection_type_code: '04' }, 'available')).toBe(false);
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
  })).toEqual({ courseCategory: '专业基础课', courseNature: '必修', generalElectiveCategory: '' });

  expect(matchAcademicGapCatalogFilters({
    name: '人文社会科学类', requirement_type: 'elective', course_natures: ['选修'],
  }, {
    course_categories: ['人文社会科学类'], course_natures: ['选修'],
  })).toEqual({ courseCategory: '人文社会科学类', courseNature: '选修', generalElectiveCategory: '' });

  expect(matchAcademicGapCatalogFilters({
    name: '人文社会科学类', path_array: ['通识类', '人文社会科学类'],
    requirement_type: 'elective', course_natures: ['选修'],
  }, {
    course_categories: ['通识选修类', '通识选修课'], course_natures: ['选修'],
    general_elective_categories: ['科学素养类', '人文社会科学类'],
  })).toEqual({
    courseCategory: '通识选修', courseNature: '选修',
    generalElectiveCategory: '人文社会科学类',
  });

  expect(matchAcademicGapCatalogFilters({
    name: '科学素养类', requirement_type: 'elective', course_natures: ['选修'],
  }, {
    course_categories: ['通识选修课'], course_natures: ['选修'],
    general_elective_categories: ['科学素养类'],
  })).toEqual({
    courseCategory: '通识选修', courseNature: '选修',
    generalElectiveCategory: '科学素养类',
  });
});
