const normalizedName = value => String(value || '').replace(/\s+/g, '').toLocaleLowerCase();

export const UNGROUPED_WEIGHT_GROUP_ID = 'ungrouped_weighted';
export const UNGROUPED_WEIGHT_GROUP_NAME = '未分组';

export const uniqueDisplayLabels = (values = [], formatter = value => value) => {
  const labels = (values || []).map(formatter).filter(Boolean);
  return [...new Set(labels)];
};

const normalizedTaxonomy = value => normalizedName(value)
  .replace(/课程/g, '')
  .replace(/课$/g, '')
  .replace(/类$/g, '')
  .replace(/模块$/g, '');

const isGeneralElective = value => ['通识选修', '通识选修类', '通识选修课', '通识选修课程']
  .some(label => normalizedName(label) === normalizedName(value));

export const isGeneralElectiveCategory = value => (
  String(value || '').replace(/\s+/g, '').includes('通识选修')
);

const campusIdentity = value => {
  const text = String(value || '').trim();
  if (text === '00' || text === '南湖校区') return '00';
  if (text === '01' || text === '浑南校区') return '01';
  return text.toLocaleLowerCase();
};

export const displayJwxkCampusName = value => {
  const text = String(value || '').trim();
  if (text === '00') return '南湖校区';
  if (text === '01') return '浑南校区';
  return text;
};

export const courseCampusLabels = course => uniqueDisplayLabels([
  course?.campus_name,
  course?.campus,
  ...(course?.schedules || []).flatMap(meeting => [meeting?.campus_name, meeting?.campus]),
], displayJwxkCampusName);

export const courseCampusIdentities = course => [...new Set([
  course?.campus || course?.campus_name,
  ...(course?.schedules || []).map(meeting => meeting?.campus || meeting?.campus_name),
].map(campusIdentity).filter(Boolean))];

const archiveTaxonomyValues = (course, key) => {
  if (key === 'courseCategory') return [
    course?.normalized_course_category,
    ...(course?.course_categories || []),
    course?.course_category,
  ];
  if (key === 'courseNature') return [course?.course_nature];
  if (key === 'generalElectiveCategory') return [course?.general_elective_category];
  if (key === 'department') return [course?.department];
  return [];
};

/** 完全基于已归档课程字段执行筛选，不触发任何官方请求。 */
export const matchesArchivedCourseFilters = (course, filters = {}) => {
  if (filters.campus && !courseCampusLabels(course).some(
    value => campusIdentity(value) === campusIdentity(filters.campus),
  )) return false;

  for (const key of ['courseNature', 'courseCategory', 'generalElectiveCategory', 'department']) {
    const selected = filters[key];
    if (!selected) continue;
    const normalize = key === 'courseCategory' ? normalizedTaxonomy : normalizedName;
    if (!archiveTaxonomyValues(course, key).some(value => (
      normalize(value) === normalize(selected)
    ))) return false;
  }

  const weekday = filters.weekday && filters.weekday !== 'all'
    ? Number(filters.weekday) : 0;
  const startSection = Number(filters.startSection || 0);
  const endSection = Number(filters.endSection || 0);
  if (weekday || startSection || endSection) {
    return (course?.schedules || []).some(meeting => {
      const meetingStart = Number(meeting?.start_section || 0);
      const meetingEnd = Number(meeting?.end_section || meetingStart || 0);
      return (!weekday || Number(meeting?.weekday || 0) === weekday)
        && (!startSection || meetingStart >= startSection)
        && (!endSection || meetingEnd <= endSection);
    });
  }
  return true;
};

export const isCrossCampusCourse = (course, currentCampus, currentCampusName = '') => {
  const homeCampus = campusIdentity(currentCampus) || campusIdentity(currentCampusName);
  if (!homeCampus) return false;
  const campuses = courseCampusIdentities(course);
  return campuses.length > 0 && campuses.some(campus => campus !== homeCampus);
};

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

export const selectionTimeConflictStatus = result => {
  if (!result || result.status !== 'conflict') return result?.status || 'unknown';
  const confirmed = (result.matches || []).some(match => (
    match?.status === 'conflict'
    && String(match?.source || '') !== 'jwxk_official'
    && Array.isArray(match?.overlapping_weeks)
    && match.overlapping_weeks.length > 0
    && Number(match?.weekday || 0) > 0
    && Number(match?.start_section || 0) > 0
    && Number(match?.end_section || 0) >= Number(match?.start_section || 0)
  ));
  return confirmed ? 'conflict' : 'unknown';
};

export const catalogGroupLiveStats = (
  group = {}, conflictMap = {}, selectionTypeCode = '',
) => {
  const classes = group.classes || [];
  // 官方 SFCT 同时包含时间冲突和跨校区，不能直接拿来统计时间冲突。
  const isConflicting = course => (
    selectionTimeConflictStatus(conflictMap[course.class_id]) === 'conflict'
  );
  return {
    conflict_free_count: classes.filter(course => !isConflicting(course)).length,
    all_classes_conflict: classes.length > 0 && classes.every(isConflicting),
    available_count: classes.filter(course => {
      const participants = selectionParticipantCount(course, selectionTypeCode);
      return course.capacity != null
        && participants != null
        && Number(course.capacity) > Number(participants);
    }).length,
  };
};

export const toggleCatalogPreviewCourse = (courses = [], course = {}) => {
  const classId = String(course.class_id || '');
  if (!classId) return courses || [];
  return (courses || []).some(item => String(item.class_id || '') === classId)
    ? (courses || []).filter(item => String(item.class_id || '') !== classId)
    : [...(courses || []), course];
};

/** 权重结果中的 0/0 记录来自其他轮次，不属于当前轮次可操作结果。 */
export const isCurrentBatchSelectionRecord = (course, selectionTypeCode = '') => {
  if (String(selectionTypeCode || course?.selection_type_code || '') !== '04') return true;
  const participants = selectionParticipantCount(course, selectionTypeCode);
  const capacity = course?.capacity == null ? null : Number(course.capacity);
  return !(Number(participants) === 0 && capacity === 0);
};

/** 当前权重轮次中已投权、但尚未归入任何方案组的教学班。 */
export const unplannedCurrentWeightSelections = (
  courses = [], planItems = [], selectionTypeCode = '',
) => {
  if (String(selectionTypeCode || '') !== '04') return [];
  const plannedClassIds = new Set((planItems || [])
    .map(item => String(item?.class_id || '').trim())
    .filter(Boolean));
  return (courses || []).filter(course => {
    const classId = String(course?.class_id || '').trim();
    return course?.selection_record_type === 'volunteered'
      && isCurrentBatchSelectionRecord(course, selectionTypeCode)
      && Boolean(classId)
      && !plannedClassIds.has(classId);
  });
};

/**
 * 将当前权重轮次中尚未属于用户方案组的已投课程，协调到持久化的“未分组”保留组。
 * 普通方案组是用户维护的候选池，不会因为课程退权而被自动删改；只有保留组随官方
 * 当前已投结果增删。返回原引用表示没有变化，便于调用方避免重复持久化和任务同步。
 */
export const reconcileUngroupedWeightPlan = (
  courses = [], planItems = [], planGroups = [], selectionTypeCode = '',
) => {
  if (String(selectionTypeCode || '') !== '04') {
    return { items: planItems, groups: planGroups, changed: false };
  }

  const classIdOf = item => String(item?.class_id || '').trim();
  const ordinaryClassIds = new Set((planItems || [])
    .filter(item => item?.plan_group_id !== UNGROUPED_WEIGHT_GROUP_ID)
    .map(classIdOf)
    .filter(Boolean));
  const currentUngrouped = (courses || []).filter(course => {
    const classId = classIdOf(course);
    return course?.selection_record_type === 'volunteered'
      && isCurrentBatchSelectionRecord(course, selectionTypeCode)
      && Boolean(classId)
      && !ordinaryClassIds.has(classId);
  });
  const currentByClassId = new Map(currentUngrouped.map(course => [classIdOf(course), course]));
  const targetCount = Math.min(20, Math.max(1, currentByClassId.size));
  const existingUngrouped = (planItems || []).filter(
    item => item?.plan_group_id === UNGROUPED_WEIGHT_GROUP_ID,
  );
  const existingByClassId = new Map(existingUngrouped.map(item => [classIdOf(item), item]));
  const nextItems = (planItems || []).flatMap(item => {
    if (item?.plan_group_id !== UNGROUPED_WEIGHT_GROUP_ID) return [item];
    const classId = classIdOf(item);
    if (!currentByClassId.has(classId)) return [];
    const live = currentByClassId.get(classId);
    const stableFields = [
      'course_code', 'course_name', 'class_number', 'teaching_class_type',
      'teacher', 'location', 'campus', 'campus_name', 'course_nature',
      'course_category', 'normalized_course_category', 'course_categories',
      'general_elective_category', 'general_elective_category_code', 'schedules',
      'devoted_weight', 'selection_source', 'selection_record_type',
    ];
    let normalized = item;
    const assign = (key, value) => {
      const meaningful = Array.isArray(value) ? value.length > 0 : value !== '' && value != null;
      const equal = typeof value === 'object'
        ? JSON.stringify(normalized[key]) === JSON.stringify(value)
        : normalized[key] === value;
      if (!meaningful || equal) return;
      if (normalized === item) normalized = { ...item };
      normalized[key] = value;
    };
    stableFields.forEach(field => assign(field, live?.[field]));
    assign('plan_group_name', UNGROUPED_WEIGHT_GROUP_NAME);
    assign('plan_group_target_count', targetCount);
    return [normalized];
  });

  currentUngrouped.forEach(course => {
    const classId = classIdOf(course);
    if (existingByClassId.has(classId)) return;
    nextItems.push({
      ...course,
      plan_group_id: UNGROUPED_WEIGHT_GROUP_ID,
      plan_group_name: UNGROUPED_WEIGHT_GROUP_NAME,
      plan_group_target_count: targetCount,
      group_id: course.group_id || course.course_code || classId,
      teaching_class_type: course.teaching_class_type || 'FANKC',
      utility: Number(course.utility || 5),
      devoted_weight: course.devoted_weight,
      selection_record_type: 'volunteered',
      selection_source: course.selection_source,
      imported_from_volunteered: true,
    });
  });

  const existingGroup = (planGroups || []).find(
    group => group?.group_id === UNGROUPED_WEIGHT_GROUP_ID,
  );
  let nextGroups = (planGroups || []).filter(
    group => group?.group_id !== UNGROUPED_WEIGHT_GROUP_ID,
  );
  if (currentByClassId.size) {
    const reservedGroup = existingGroup
      && existingGroup.name === UNGROUPED_WEIGHT_GROUP_NAME
      && Number(existingGroup.target_count || 1) === targetCount
      ? existingGroup
      : {
        ...(existingGroup || {}),
        group_id: UNGROUPED_WEIGHT_GROUP_ID,
        name: UNGROUPED_WEIGHT_GROUP_NAME,
        target_count: targetCount,
      };
    const insertionIndex = existingGroup ? (planGroups || []).indexOf(existingGroup) : -1;
    if (insertionIndex >= 0) nextGroups.splice(insertionIndex, 0, reservedGroup);
    else nextGroups.push(reservedGroup);
  }

  const changed = nextItems.length !== (planItems || []).length
    || nextGroups.length !== (planGroups || []).length
    || nextItems.some((item, index) => item !== planItems[index])
    || nextGroups.some((group, index) => group !== planGroups[index]);
  return changed
    ? { items: nextItems, groups: nextGroups, changed: true }
    : { items: planItems, groups: planGroups, changed: false };
};

const comparableBatchTime = value => {
  const timestamp = Date.parse(String(value || ''));
  return Number.isFinite(timestamp) ? timestamp : String(value || '').trim();
};

export const changedOfficialBatchTimes = (previousBatches = [], nextBatches = []) => {
  const previousByCode = new Map((previousBatches || []).map(batch => [String(batch.code || ''), batch]));
  return (nextBatches || []).flatMap(batch => {
    const previous = previousByCode.get(String(batch.code || ''));
    if (!previous || !batch.begin_time || !batch.end_time) return [];
    const startChanged = comparableBatchTime(previous.begin_time) !== comparableBatchTime(batch.begin_time);
    const endChanged = comparableBatchTime(previous.end_time) !== comparableBatchTime(batch.end_time);
    return startChanged || endChanged ? [{
      batch_code: String(batch.code || ''),
      batch_name: batch.name || previous.name || '选课轮次',
      old_start_at: previous.begin_time || '',
      old_end_at: previous.end_time || '',
      start_at: batch.begin_time,
      end_at: batch.end_time,
    }] : [];
  });
};

/** 培养计划缺口同时计入本轮确认选中和已投权课程，排除其他轮次的只读记录。 */
export const academicPlanSelectionRecords = (courses = [], selectionTypeCode = '') => {
  const byCourse = new Map((courses || [])
    .filter(course => isCurrentBatchSelectionRecord(course, selectionTypeCode))
    .map(course => [
      String(course.course_code || '').trim().toUpperCase()
        || `name:${String(course.course_name || '').trim().replace(/\s+/g, '').toLowerCase()}`,
      course,
    ]));
  return [...byCourse.entries()]
    .filter(([identity]) => identity && identity !== 'name:')
    .map(([, course]) => course);
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
  const matchedCourseCategory = matchOption(categoryCandidates, availableCategories);
  const generalElectiveGap = Boolean(matchedGeneralCategory) || categoryCandidates.some(value => (
    isGeneralElective(value) || /通识/.test(String(value))
  ));
  const preferredNature = gap?.requirement_type === 'elective'
    ? '选修'
    : gap?.requirement_type === 'required'
      ? '必修'
      : (gap?.course_natures || [])[0] || '';
  return {
    courseCategory: matchedCourseCategory
      || (generalElectiveGap && availableCategories.some(isGeneralElective) ? '通识选修' : '')
      || String(categoryCandidates[0] || ''),
    courseNature: matchOption([preferredNature, ...(gap?.course_natures || [])], availableNatures)
      || preferredNature,
    generalElectiveCategory: generalElectiveGap ? matchedGeneralCategory : '',
    gapCategoryMatched: Boolean(matchedCourseCategory || matchedGeneralCategory),
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

const isTaskRecommendedCourse = course => (
  (course?.source_scopes || []).some(value => String(value || '').toUpperCase() === 'TJKC')
  || (course?.source_tags || []).some(value => (
    String(value || '').toUpperCase() === 'TJKC'
    || String(value || '').includes('任务推荐班')
  ))
);

const isTaskRecommendedGroup = group => (
  isTaskRecommendedCourse(group)
  || (group?.classes || []).some(isTaskRecommendedCourse)
);

/** 保留所有课程，只把不可选教学班和只含不可选班的课程组放到末尾。 */
export const sortCatalogGroupsBySelectability = groups => [...(groups || [])]
  .map(group => ({
    ...group,
    classes: [...(group.classes || [])].sort((left, right) => (
      catalogClassRank(left) - catalogClassRank(right)
      || Number(isTaskRecommendedCourse(right)) - Number(isTaskRecommendedCourse(left))
      || Number(selectionParticipantCount(left) || 0) - Number(selectionParticipantCount(right) || 0)
      || String(left.teacher || '').localeCompare(String(right.teacher || ''), 'zh-CN')
    )),
  }))
  .sort((left, right) => (
    Math.min(...(left.classes || []).map(catalogClassRank), 4)
    - Math.min(...(right.classes || []).map(catalogClassRank), 4)
    || Number(isTaskRecommendedGroup(right)) - Number(isTaskRecommendedGroup(left))
  ));

const catalogCourseIdentity = group => {
  const code = String(group?.course_code || '').trim().toLocaleLowerCase();
  if (code) return `code:${code}`;
  const fallback = `${normalizedName(group?.course_name)}|${String(group?.credits || '').trim()}|${String(group?.department || '').trim().toLocaleLowerCase()}`;
  return fallback !== '||' ? `name:${fallback}` : `group:${String(group?.group_id || '').trim().toLocaleLowerCase()}`;
};

const mergeCatalogGroupsByCourse = groups => {
  const merged = [];
  const byIdentity = new Map();
  (groups || []).forEach(group => {
    const identity = catalogCourseIdentity(group);
    const previous = byIdentity.get(identity);
    if (!previous) {
      const copy = { ...group, classes: [...(group.classes || [])] };
      byIdentity.set(identity, copy);
      merged.push(copy);
      return;
    }
    const classMap = new Map((previous.classes || []).map(course => [catalogClassKey(course), course]));
    (group.classes || []).forEach(course => {
      const key = catalogClassKey(course);
      if (!key || !classMap.has(key)) {
        previous.classes.push(course);
        if (key) classMap.set(key, course);
        return;
      }
      const existing = classMap.get(key);
      const replacement = { ...existing, ...course };
      classMap.set(key, replacement);
      const index = previous.classes.indexOf(existing);
      if (index >= 0) previous.classes[index] = replacement;
    });
    for (const key of ['source_tags', 'source_scopes', 'course_categories', 'campuses']) {
      const values = [...(previous[key] || []), ...(group[key] || [])].filter(Boolean);
      if (values.length) previous[key] = [...new Set(values)];
    }
    for (const key of ['course_name', 'course_code', 'credits', 'hours', 'department', 'course_nature', 'course_category', 'normalized_course_category', 'general_elective_category', 'general_elective_category_code', 'exam_type', 'exam_type_code', 'score_scale', 'score_scale_code']) {
      if (!previous[key] && group[key]) previous[key] = group[key];
    }
    previous.class_count = previous.classes.length;
  });
  return merged;
};

export const catalogGroupsForDisplay = (groups = [], {
  availability = 'all',
  weekday = 'all',
} = {}) => {
  return sortCatalogGroupsBySelectability(mergeCatalogGroupsByCourse((groups || []).map(group => {
    const classes = (group.classes || []).filter(course => {
      if (!matchesCatalogAvailability(course, availability)) return false;
      if (weekday !== 'all' && !(course.schedules || []).some(item => String(item.weekday) === String(weekday))) {
        return false;
      }
      return true;
    });
    return { ...group, classes, class_count: classes.length };
  }).filter(group => group.classes.length)));
};

const catalogGroupKey = group => String(group?.course_code || group?.group_id || group?.course_name || '')
  .trim().toLocaleLowerCase();
const catalogClassKey = course => String(course?.class_id || course?.teaching_class_id || '');

export const createCatalogDisplayLayout = groups => mergeCatalogGroupsByCourse(groups).map(group => ({
  group_id: catalogGroupKey(group),
  class_ids: (group.classes || []).map(catalogClassKey).filter(Boolean),
})).filter(item => item.group_id);

export const extendCatalogDisplayLayout = (layout = [], groups = []) => {
  const next = (layout || []).map(item => ({ ...item, class_ids: [...(item.class_ids || [])] }));
  const byGroup = new Map(next.map(item => [item.group_id, item]));
  (groups || []).forEach(group => {
    const groupId = catalogGroupKey(group);
    if (!groupId) return;
    let entry = byGroup.get(groupId);
    if (!entry) {
      entry = { group_id: groupId, class_ids: [] };
      next.push(entry);
      byGroup.set(groupId, entry);
    }
    const known = new Set(entry.class_ids);
    (group.classes || []).forEach(course => {
      const classId = catalogClassKey(course);
      if (classId && !known.has(classId)) {
        entry.class_ids.push(classId);
        known.add(classId);
      }
    });
  });
  return next;
};

export const applyCatalogDisplayLayout = (groups = [], layout = []) => {
  const groupMap = new Map(mergeCatalogGroupsByCourse(groups).map(group => [catalogGroupKey(group), group]));
  return (layout || []).map(entry => {
    const group = groupMap.get(entry.group_id);
    if (!group) return null;
    const classMap = new Map((group.classes || []).map(course => [catalogClassKey(course), course]));
    const classes = (entry.class_ids || []).map(classId => classMap.get(classId)).filter(Boolean);
    return classes.length ? { ...group, classes, class_count: classes.length } : null;
  }).filter(Boolean);
};

export const mergeCatalogRefreshPreservingOrder = (previous = [], incoming = []) => {
  const incomingMap = new Map((incoming || []).map(group => [catalogGroupKey(group), group]));
  const previousKeys = new Set((previous || []).map(catalogGroupKey));
  const merged = (previous || []).map(group => {
    const nextGroup = incomingMap.get(catalogGroupKey(group));
    if (!nextGroup) return group;
    const nextClasses = new Map((nextGroup.classes || []).map(course => [catalogClassKey(course), course]));
    const oldClassKeys = new Set((group.classes || []).map(catalogClassKey));
    return {
      ...group,
      ...nextGroup,
      classes: [
        ...(group.classes || []).map(course => ({
          ...course,
          ...(nextClasses.get(catalogClassKey(course)) || {}),
        })),
        ...(nextGroup.classes || []).filter(course => !oldClassKeys.has(catalogClassKey(course))),
      ],
    };
  });
  return [
    ...merged,
    ...(incoming || []).filter(group => !previousKeys.has(catalogGroupKey(group))),
  ];
};

export const sameSelectionCourse = (left, right) => {
  const leftCode = String(left.course_code || '').trim().toLocaleLowerCase();
  const rightCode = String(right.course_code || '').trim().toLocaleLowerCase();
  if (leftCode && rightCode) return leftCode === rightCode;
  return Boolean(normalizedName(left.course_name) && normalizedName(left.course_name) === normalizedName(right.course_name));
};

const sameSelectionRecord = (left, right) => {
  const leftClassId = String(left?.class_id || '').trim();
  const rightClassId = String(right?.class_id || '').trim();
  if (leftClassId && rightClassId) return leftClassId === rightClassId;
  return sameSelectionCourse(left || {}, right || {});
};

export const upsertSelectionRecord = (records = [], record = {}) => [
  ...(records || []).filter(item => !sameSelectionRecord(item, record)),
  record,
];

export const removeSelectionRecord = (records = [], target = {}) => (
  (records || []).filter(item => !sameSelectionRecord(item, target))
);

export const patchCatalogSelection = (groups = [], target = {}, {
  selected = false,
  devotedWeight = null,
} = {}) => (groups || []).map(group => {
  const groupMatches = sameSelectionCourse(group, target)
    || (group.classes || []).some(course => sameSelectionCourse(course, target));
  if (!groupMatches) return group;
  return {
    ...group,
    classes: (group.classes || []).map(course => ({
      ...course,
      selected: selected && sameSelectionRecord(course, target),
      course_already_selected: selected,
      devoted_weight: selected && sameSelectionRecord(course, target) ? devotedWeight : null,
    })),
  };
});

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

/** 远端写操作前使用教学班 ID 精确定位，禁止按同课程代码回退到另一个备选班。 */
export const findExactSelectionClassRecord = (records = [], target = {}) => {
  const classId = String(target?.class_id || '').trim();
  if (!classId) return null;
  return records.find(item => String(item?.class_id || '').trim() === classId) || null;
};

const overlappingWeeks = (left, right) => {
  const leftWeeks = new Set((left.weeks || []).map(Number));
  return [...new Set((right.weeks || []).map(Number).filter(week => leftWeeks.has(week)))].sort((a, b) => a - b);
};

export const mergeSelectionConflictMatches = (matches = []) => {
  const merged = new Map();
  matches.filter(match => match && !['clear', 'unknown'].includes(match.status)).forEach(match => {
    // The same course can arrive once from JWXT and once from a JWXK overlay.
    // One source may omit the code or use another teaching-class id, so use
    // the stable display name first while still keeping split meeting times.
    const identity = String(match.baseline_course_name || match.baseline_course_code || 'unknown')
      .replace(/\s+/g, '').toLowerCase();
    const key = [
      identity,
      Number(match.weekday || 0),
      Number(match.start_section || 0),
      Number(match.end_section || 0),
    ].join(':');
    const previous = merged.get(key);
    if (!previous) {
      merged.set(key, {
        ...match,
        baseline_weeks: [...new Set((match.baseline_weeks || []).map(Number))].sort((a, b) => a - b),
        overlapping_weeks: [...new Set((match.overlapping_weeks || []).map(Number))].sort((a, b) => a - b),
      });
      return;
    }
    merged.set(key, {
      ...previous,
      baseline_course_code: previous.baseline_course_code || match.baseline_course_code || '',
      baseline_teaching_class_id: previous.baseline_teaching_class_id
        || match.baseline_teaching_class_id || '',
      baseline_weeks: [...new Set([
        ...(previous.baseline_weeks || []), ...(match.baseline_weeks || []),
      ].map(Number))].sort((a, b) => a - b),
      overlapping_weeks: [...new Set([
        ...(previous.overlapping_weeks || []), ...(match.overlapping_weeks || []),
      ].map(Number))].sort((a, b) => a - b),
    });
  });
  return [...merged.values()];
};

export const selectionConflictMatchReferencesCourse = (match = {}, course = {}) => {
  const baselineClassId = String(match.baseline_teaching_class_id || '').trim();
  const courseClassIds = [course.class_id, course.teaching_class_id, course.source_id]
    .map(value => String(value || '').trim()).filter(Boolean);
  if (baselineClassId && courseClassIds.includes(baselineClassId)) return true;
  return sameSelectionCourse({
    course_code: match.baseline_course_code,
    course_name: match.baseline_course_name,
  }, course);
};

/** Remove obsolete baseline matches immediately after a successful withdrawal. */
export const removeCourseFromSelectionConflictMap = (conflictMap = {}, course = {}) => (
  Object.fromEntries(Object.entries(conflictMap || {}).map(([key, result]) => {
    const matches = mergeSelectionConflictMatches((result?.matches || []).filter(
      match => !selectionConflictMatchReferencesCourse(match, course),
    ));
    return [key, {
      ...result,
      status: matches.length ? 'conflict' : result?.status === 'unknown' ? 'unknown' : 'clear',
      matches,
    }];
  }))
);

export const immediateSelectionConflictMap = (personalCourses = [], candidateCourses = []) => Object.fromEntries(
  candidateCourses.map(candidate => {
    const candidateComplete = (candidate.weeks || []).length && candidate.weekday
      && candidate.start_section && candidate.end_section;
    let hasUnknownBaseline = !candidateComplete;
    const matches = mergeSelectionConflictMatches(personalCourses.flatMap(personal => {
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
        baseline_course_code: personal.course_code || '',
        baseline_teaching_class_id: personal.teaching_class_id || personal.source_id || '',
        baseline_weeks: personal.weeks || [],
        status: 'conflict',
        source: personal.layer ? 'selection_candidate_local' : 'personal_timetable_local',
        overlapping_weeks: weeks,
        weekday: candidate.weekday,
        start_section: Math.max(personal.start_section, candidate.start_section),
        end_section: Math.min(personal.end_section, candidate.end_section),
      }];
    }));
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
      matches: mergeSelectionConflictMatches(nextMatches),
    };
  }, { status: 'clear', matches: [] });
  return [course.class_id, summary];
}));
