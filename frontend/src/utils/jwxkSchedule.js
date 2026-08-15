const normalizedName = value => String(value || '').replace(/\s+/g, '').toLocaleLowerCase();

const normalizedTaxonomy = value => normalizedName(value)
  .replace(/课程/g, '')
  .replace(/课$/g, '')
  .replace(/类$/g, '')
  .replace(/模块$/g, '');

const isGeneralElective = value => ['通识选修', '通识选修类', '通识选修课', '通识选修课程']
  .some(label => normalizedName(label) === normalizedName(value));

export const mergeCatalogFilterLayers = (manual = {}, plan = {}, keys = []) => Object.fromEntries(
  keys.map(key => [key, manual[key] || plan[key] || '']),
);

export const selectionParticipantCount = (course, selectionTypeCode = '') => {
  if (course?.market_participant_count != null) return Number(course.market_participant_count);
  const effectiveType = String(selectionTypeCode || course?.selection_type_code || '');
  const value = effectiveType === '04' ? course?.weight_participant_count : course?.selected_count;
  return value == null ? null : Number(value);
};

export const selectionParticipantLabel = (course, selectionTypeCode = '') => (
  course?.market_participant_label
  || (String(selectionTypeCode || course?.selection_type_code || '') === '04' ? '已投注人数' : '已选人数')
);

/** 权重结果中的 0/0 记录来自其他轮次，不属于当前轮次可操作结果。 */
export const isCurrentBatchSelectionRecord = (course, selectionTypeCode = '') => {
  if (String(selectionTypeCode || course?.selection_type_code || '') !== '04') return true;
  const participants = selectionParticipantCount(course, selectionTypeCode);
  const capacity = course?.capacity == null ? null : Number(course.capacity);
  return !(Number(participants) === 0 && capacity === 0);
};

export const matchAcademicGapCatalogFilters = (gap, filterOptions = {}) => {
  const availableCategories = (filterOptions.course_categories || []).map(item => (
    typeof item === 'string' ? item : item.value
  )).filter(Boolean);
  const availableNatures = (filterOptions.course_natures || []).map(item => (
    typeof item === 'string' ? item : item.value
  )).filter(Boolean);
  const availableGeneralCategories = (filterOptions.general_elective_categories || []).map(item => (
    typeof item === 'string' ? item : item.value
  )).filter(Boolean);
  const categoryCandidates = [
    gap?.originalName,
    gap?.name,
    ...[...(gap?.path_array || [])].reverse(),
  ].filter(value => value && !['必修', '选修', '课程'].includes(String(value).trim()));
  const matchOption = (candidates, options) => {
    for (const candidate of candidates) {
      const exact = options.find(option => normalizedName(option) === normalizedName(candidate));
      if (exact) return exact;
    }
    for (const candidate of candidates) {
      const normalizedCandidate = normalizedTaxonomy(candidate);
      if (!normalizedCandidate) continue;
      const equivalent = options.find(option => normalizedTaxonomy(option) === normalizedCandidate);
      if (equivalent) return equivalent;
    }
    return '';
  };
  const matchedGeneralCategory = matchOption(categoryCandidates, availableGeneralCategories);
  const generalElectiveGap = Boolean(matchedGeneralCategory) || categoryCandidates.some(value => (
    isGeneralElective(value) || /通识/.test(String(value))
  ));
  const preferredNature = gap?.requirement_type === 'elective'
    ? '选修'
    : gap?.requirement_type === 'required'
      ? '必修'
      : (gap?.course_natures || [])[0] || '';
  return {
    courseCategory: matchOption(categoryCandidates, availableCategories)
      || (generalElectiveGap && availableCategories.some(isGeneralElective) ? '通识选修' : '')
      || String(categoryCandidates[0] || ''),
    courseNature: matchOption([preferredNature, ...(gap?.course_natures || [])], availableNatures)
      || preferredNature,
    generalElectiveCategory: generalElectiveGap ? matchedGeneralCategory : '',
  };
};

export const matchesCatalogAvailability = (course, availability, selectionTypeCode = '') => {
  if (availability === 'selectable') return course.eligibility_status === 'selectable';
  if (availability === 'available') return (
    course.capacity != null && selectionParticipantCount(course, selectionTypeCode) != null
    && Number(course.capacity) > selectionParticipantCount(course, selectionTypeCode)
  );
  if (availability === 'conflict_free') return !course.conflict;
  if (availability === 'selected') return Boolean(course.selected || course.course_already_selected);
  return true;
};

export const catalogAvailabilityRemoteFilters = availability => ({
  ...(availability === 'available' ? { SFYM: '0' } : {}),
  ...(availability === 'conflict_free' ? { SFCT: '0' } : {}),
  ...(availability === 'selected' ? { SFYX: '1' } : {}),
});

// “本轮可选”来自逐教学班资格核验，不是课程列表接口的原生筛选字段。
// 切换时必须保留当前已核验结果，不能重新加载目录并丢失它们。
export const catalogAvailabilityRequestMode = availability => (
  availability === 'selectable' ? 'all' : availability
);

export const academicGapCatalogScope = scopes => (
  (scopes || []).some(item => (item.code || item.value) === 'ALL')
    ? 'ALL'
    : (scopes || []).some(item => (item.code || item.value) === 'ALLKC') ? 'ALLKC' : 'ROUND'
);

export const inferBatchRequirementType = batch => {
  const infer = value => {
    const text = String(value || '');
    const hasRequired = /必修/.test(text);
    const hasElective = /选修/.test(text);
    if (hasRequired !== hasElective) return hasRequired ? 'required' : 'elective';
    return '';
  };
  return infer(batch?.name) || infer(batch?.notice) || '';
};

export const filterAcademicPlanGapsForBatch = (gaps, batch) => {
  const requirementType = inferBatchRequirementType(batch);
  if (!requirementType) return gaps || [];
  const oppositeType = requirementType === 'required' ? 'elective' : 'required';
  return (gaps || []).filter(gap => gap.requirement_type !== oppositeType);
};

const catalogClassRank = course => {
  const unavailable = course.eligibility_status === 'unavailable'
    || course.full || course.restricted;
  if (unavailable) return 3;
  if (course.eligibility_status === 'unknown') return 2;
  if (course.conflict) return 1;
  return 0;
};

/** 保留所有课程，只把不可选教学班和只含不可选班的课程组放到末尾。 */
export const sortCatalogGroupsBySelectability = groups => [...(groups || [])]
  .map(group => ({
    ...group,
    classes: [...(group.classes || [])].sort((left, right) => (
      catalogClassRank(left) - catalogClassRank(right)
      || Number(selectionParticipantCount(left) || 0) - Number(selectionParticipantCount(right) || 0)
      || String(left.teacher || '').localeCompare(String(right.teacher || ''), 'zh-CN')
    )),
  }))
  .sort((left, right) => (
    Math.min(...(left.classes || []).map(catalogClassRank), 4)
    - Math.min(...(right.classes || []).map(catalogClassRank), 4)
  ));

export const sameSelectionCourse = (left, right) => {
  const leftCode = String(left.course_code || '').trim().toLocaleLowerCase();
  const rightCode = String(right.course_code || '').trim().toLocaleLowerCase();
  if (leftCode && rightCode) return leftCode === rightCode;
  return Boolean(normalizedName(left.course_name) && normalizedName(left.course_name) === normalizedName(right.course_name));
};

/**
 * 官方写接口的 code=200 只代表请求被受理。只有已选/已投列表中出现
 * 对应教学班（或同课程代码）时，页面才能把操作显示为成功。
 */
export const findMatchingSelectionRecord = (records = [], target = {}) => {
  const classId = String(target.class_id || '').trim();
  if (classId) {
    const exact = records.find(item => String(item.class_id || '').trim() === classId);
    if (exact) return exact;
  }
  return records.find(item => sameSelectionCourse(item, target)) || null;
};

const overlappingWeeks = (left, right) => {
  const leftWeeks = new Set((left.weeks || []).map(Number));
  return [...new Set((right.weeks || []).map(Number).filter(week => leftWeeks.has(week)))].sort((a, b) => a - b);
};

export const immediateSelectionConflictMap = (personalCourses = [], candidateCourses = []) => Object.fromEntries(
  candidateCourses.map(candidate => {
    const candidateComplete = (candidate.weeks || []).length && candidate.weekday
      && candidate.start_section && candidate.end_section;
    let hasUnknownBaseline = !candidateComplete;
    const matches = personalCourses.flatMap(personal => {
      if (sameSelectionCourse(personal, candidate)) return [];
      if (personal.term_code && candidate.term_code && personal.term_code !== candidate.term_code) return [];
      const weeks = overlappingWeeks(personal, candidate);
      const personalComplete = (personal.weeks || []).length && personal.weekday
        && personal.start_section && personal.end_section;
      if (!personalComplete) {
        hasUnknownBaseline = true;
        return [];
      }
      if (!candidateComplete || personal.weekday !== candidate.weekday || !weeks.length
        || personal.end_section < candidate.start_section
        || candidate.end_section < personal.start_section) return [];
      return [{
        baseline_meeting_id: personal.meeting_id || personal.id,
        baseline_course_name: personal.course_name,
        status: 'conflict',
        source: 'personal_timetable_local',
        overlapping_weeks: weeks,
        weekday: candidate.weekday,
        start_section: Math.max(personal.start_section, candidate.start_section),
        end_section: Math.min(personal.end_section, candidate.end_section),
      }];
    });
    return [candidate.meeting_id || candidate.id, {
      status: matches.length ? 'conflict' : hasUnknownBaseline ? 'unknown' : 'clear',
      matches,
    }];
  }),
);

const conflictStatusRank = { clear: 0, unknown: 1, conflict: 2 };

/**
 * 将逐课次冲突结果汇总到教学班。课程目录使用该结果常驻展示冲突状态，
 * 悬停只负责预览与展示详情，不能再作为开始计算的条件。
 */
export const summarizeSelectionConflictsByClass = (
  courses = [], meetingConflicts = {}, { baselineReady = true } = {},
) => Object.fromEntries(courses.map(course => {
  const meetings = course.meetings || course.schedules || [];
  if (!baselineReady || !meetings.length) {
    return [course.class_id, { status: 'unknown', matches: [] }];
  }
  const summary = meetings.reduce((result, meeting) => {
    const conflict = meetingConflicts[meeting.meeting_id || meeting.id] || {
      status: 'unknown', matches: [],
    };
    const nextMatches = [...(result.matches || []), ...(conflict.matches || [])];
    return {
      status: conflictStatusRank[conflict.status] > conflictStatusRank[result.status]
        ? conflict.status
        : result.status,
      matches: [...new Map(nextMatches.map(match => [
        [
          match.baseline_meeting_id,
          match.source,
          match.weekday,
          match.start_section,
          match.end_section,
        ].join(':'),
        match,
      ])).values()],
    };
  }, { status: 'clear', matches: [] });
  return [course.class_id, summary];
}));
