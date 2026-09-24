const hasValue = value => value !== null && value !== undefined && value !== '';

const displayValue = value => hasValue(value) ? String(value) : '无';

const fieldValue = (field, value) => {
  if (!hasValue(value)) return '无';
  if (field === 'is_passed') return value ? '已通过' : '未通过';
  if (field === 'is_selected') return value ? '已选' : '未选';
  if (field === 'is_planned') return value ? '计划内' : '非计划内';
  if (field === 'is_core') return value ? '核心课' : '非核心课';
  if (Array.isArray(value)) return value.join(' > ') || '无';
  return displayValue(value);
};

const courseKey = course => {
  const code = course?.code || course?.course_code || course?.courseCode || '';
  const term = course?.term || course?.term_code || course?.select_term_code
    || course?.termCode || '';
  if (code) return `${code}::${term}`;
  return `legacy::${JSON.stringify({
    term,
    name: course?.name || course?.course_name || course?.courseName || '',
    credit: course?.credit ?? '',
    nature: course?.course_type || course?.course_nature || '',
    category: course?.course_category || course?.category_path || '',
  })}`;
};

const courseName = course => (
  course?.name || course?.course_name || course?.courseName
  || course?.code || course?.course_code || '未命名课程'
);

const listNames = (courses, limit = 3) => {
  const names = courses.slice(0, limit).map(courseName);
  const remaining = courses.length - names.length;
  return `${names.join('、')}${remaining > 0 ? `等 ${courses.length} 门` : ''}`;
};

const mapCourses = courses => {
  const result = new Map();
  const occurrences = new Map();
  (courses || []).filter(Boolean).forEach(course => {
    const baseKey = courseKey(course);
    const occurrence = occurrences.get(baseKey) || 0;
    occurrences.set(baseKey, occurrence + 1);
    result.set(`${baseKey}::${occurrence}`, course);
  });
  return result;
};

const changedFields = (before, after, fields) => fields.filter(
  field => String(before?.[field] ?? '') !== String(after?.[field] ?? ''),
);

const appendCourseChanges = (items, beforeCourses, afterCourses, fields, labels) => {
  const beforeMap = mapCourses(beforeCourses);
  const afterMap = mapCourses(afterCourses);
  const added = [...afterMap.entries()]
    .filter(([key]) => !beforeMap.has(key))
    .map(([, course]) => course);
  const removed = [...beforeMap.entries()]
    .filter(([key]) => !afterMap.has(key))
    .map(([, course]) => course);
  const changed = [...afterMap.entries()]
    .filter(([key, course]) => (
      beforeMap.has(key) && changedFields(beforeMap.get(key), course, fields).length
    ))
    .map(([key, course]) => ({ before: beforeMap.get(key), after: course }));

  if (added.length) items.push(`新增课程：${listNames(added)}`);
  if (removed.length) items.push(`移除课程：${listNames(removed)}`);
  changed.slice(0, 3).forEach(({ before, after }) => {
    const changes = changedFields(before, after, fields)
      .slice(0, 3)
      .map(field => `${labels[field]} ${fieldValue(field, before[field])} → ${fieldValue(field, after[field])}`);
    items.push(`${courseName(after)}：${changes.join('，')}`);
  });
  if (changed.length > 3) items.push(`另有 ${changed.length - 3} 门课程信息发生变化`);

  return { added: added.length, removed: removed.length, changed: changed.length };
};

export const summarizeScoreUpdate = (beforePayload, afterPayload) => {
  const items = [];
  const beforeScores = beforePayload?.scores || [];
  const afterScores = afterPayload?.scores || [];
  appendCourseChanges(
    items,
    beforeScores,
    afterScores,
    [
      'score', 'gpa', 'is_passed', 'credit', 'name', 'course_type',
      'course_category', 'general_category', 'exam_type', 'exam_status',
      'course_nature', 'term', 'term_display',
    ],
    {
      score: '成绩',
      gpa: '绩点',
      credit: '学分',
      is_passed: '通过状态',
      name: '课程名称',
      course_type: '课程性质',
      course_category: '课程类别',
      general_category: '通识类别',
      exam_type: '考核方式',
      exam_status: '考试状态',
      course_nature: '课程属性',
      term: '学期',
      term_display: '学期',
    },
  );

  const beforeGpa = beforePayload?.overall_gpa;
  const afterGpa = afterPayload?.overall_gpa;
  if (String(beforeGpa ?? '') !== String(afterGpa ?? '')) {
    items.push(`总 GPA：${displayValue(beforeGpa)} → ${displayValue(afterGpa)}`);
  }

  if (!items.length && beforeScores.length !== afterScores.length) {
    items.push(`课程总数：${beforeScores.length} → ${afterScores.length}`);
  }
  return items.length ? items : ['成绩数据内容发生变化'];
};

const mapTimetableMeetings = courses => {
  const result = new Map();
  const occurrences = new Map();
  (courses || []).filter(Boolean).forEach(course => {
    const base = course.meeting_id || course.id || [
      course.course_code || course.course_name || '',
      course.weekday ?? '',
      course.start_section ?? '',
      course.end_section ?? '',
    ].join(':');
    const ordinal = occurrences.get(base) || 0;
    occurrences.set(base, ordinal + 1);
    result.set(`${base}::${ordinal}`, course);
  });
  return result;
};

const timetableValue = (value) => (
  Array.isArray(value) ? value.join('、') : String(value ?? '')
);

export const summarizeTimetableUpdate = (beforePayload, afterPayload) => {
  const before = mapTimetableMeetings(beforePayload?.courses);
  const after = mapTimetableMeetings(afterPayload?.courses);
  const items = [];
  const name = course => course?.course_name || course?.course_code || '未命名课程';
  const added = [...after].filter(([key]) => !before.has(key)).map(([, course]) => course);
  const removed = [...before].filter(([key]) => !after.has(key)).map(([, course]) => course);
  const paired = [];
  const fallbackKey = course => String(course?.course_code || course?.course_name || '').trim();
  // Missing official class IDs can change when a section changes. Pair only
  // unambiguous one-to-one courses; multiple meetings retain add/remove labels.
  for (let index = removed.length - 1; index >= 0; index -= 1) {
    const key = fallbackKey(removed[index]);
    if (!key) continue;
    const sameOld = removed.filter(course => fallbackKey(course) === key);
    const sameNew = added.filter(course => fallbackKey(course) === key);
    if (sameOld.length !== 1 || sameNew.length !== 1) continue;
    const next = added.indexOf(sameNew[0]);
    paired.push({ previous: removed[index], course: added[next] });
    removed.splice(index, 1);
    added.splice(next, 1);
  }
  if (added.length) items.push(`新增课程：${listNames(added)}`);
  if (removed.length) items.push(`移除课程：${listNames(removed)}`);

  const fields = [
    ['location', '地点'], ['weekday', '星期'], ['start_section', '开始节次'],
    ['end_section', '结束节次'], ['weeks', '周次'], ['week_text', '周次说明'],
    ['start_time', '开始时间'], ['end_time', '结束时间'],
    ['teachers', '教师'], ['course_name', '课程名称'],
  ];
  const changed = [...after].filter(([key, course]) => (
    before.has(key)
    && JSON.stringify(before.get(key)) !== JSON.stringify(course)
  )).map(([key, course]) => ({ previous: before.get(key), course })).concat(paired);
  changed.slice(0, 3).forEach(({ previous, course }) => {
    const details = fields.filter(([field]) => (
      timetableValue(previous[field]) !== timetableValue(course[field])
    )).slice(0, 3).map(([field, label]) => (
      `${label} ${displayValue(timetableValue(previous[field]))} → ${displayValue(timetableValue(course[field]))}`
    ));
    items.push(`${name(course)}：${details.length ? details.join('，') : '课程安排信息有调整'}`);
  });
  if (changed.length > 3) items.push(`另有 ${changed.length - 3} 条课程安排发生变化`);

  if (JSON.stringify(beforePayload?.unscheduled || []) !== JSON.stringify(afterPayload?.unscheduled || [])) {
    items.push('未安排节次的课程发生变化');
  }
  if (JSON.stringify(beforePayload?.practices || []) !== JSON.stringify(afterPayload?.practices || [])) {
    items.push('集中实践安排发生变化');
  }
  if (['campuses', 'weeks', 'sections_by_campus'].some(field => (
    JSON.stringify(beforePayload?.[field] || []) !== JSON.stringify(afterPayload?.[field] || [])
  ))) items.push('校区、教学周或节次配置发生变化');
  if (!items.length) items.push('课表其他内容发生变化');
  return items;
};

const collectAcademicCourses = payload => {
  const courses = [];
  const visit = nodes => (nodes || []).forEach(node => {
    if (Array.isArray(node?.courses)) {
      courses.push(...node.courses.map(course => ({
        ...course,
        category_path: course?.category_path || node?.path || node?.path_array || '',
      })));
    }
    visit(node?.children);
  });
  visit(payload?.categories);
  if (Array.isArray(payload?.outside_courses)) courses.push(...payload.outside_courses);
  return [...mapCourses(courses).values()];
};

const CREDIT_FIELDS = [
  ['total_required', '要求学分'],
  ['total_passed', '已修学分'],
  ['total_selected', '已选学分'],
  ['total_earned', '已获得学分'],
  ['total_remaining', '待修学分'],
  ['completion_rate', '完成比例'],
];

export const summarizeAcademicReportUpdate = (beforePayload, afterPayload) => {
  const items = [];
  const beforeSummary = beforePayload?.credit_summary || {};
  const afterSummary = afterPayload?.credit_summary || {};

  CREDIT_FIELDS.forEach(([field, label]) => {
    if (String(beforeSummary[field] ?? '') !== String(afterSummary[field] ?? '')) {
      items.push(
        `${label}：${displayValue(beforeSummary[field])} → ${displayValue(afterSummary[field])}`,
      );
    }
  });

  const courseCounts = appendCourseChanges(
    items,
    collectAcademicCourses(beforePayload),
    collectAcademicCourses(afterPayload),
    [
      'status', 'is_passed', 'is_selected', 'is_planned', 'credit',
      'score', 'status_display', 'course_nature', 'category_path', 'term_code',
      'select_term_code', 'exam_type', 'is_core', 'substitute_course_name',
      'substitute_credit', 'dept_code', 'course_name',
    ],
    {
      status: '状态',
      is_passed: '通过状态',
      is_selected: '选课状态',
      is_planned: '计划状态',
      credit: '学分',
      score: '成绩',
      status_display: '状态说明',
      course_nature: '课程性质',
      category_path: '类别路径',
      term_code: '学期',
      select_term_code: '选课学期',
      exam_type: '考核方式',
      is_core: '核心课属性',
      substitute_course_name: '替代课程',
      substitute_credit: '替代学分',
      dept_code: '开课单位',
      course_name: '课程名称',
    },
  );

  const stableCategoryTree = payload => {
    const sortKey = node => [
      String(node?.wid || ''), String(node?.course_group_wid || ''),
      String(node?.category_code || ''), String(node?.path || ''), String(node?.name || ''),
    ].join('\u001f');
    const sortCourse = course => [
      String(course?.course_code || ''), String(course?.course_name || ''),
      String(course?.term_code || ''),
    ].join('\u001f');
    const normalize = node => ({
      ...node,
      courses: [...(node?.courses || [])].sort((a, b) => sortCourse(a).localeCompare(sortCourse(b))),
      children: [...(node?.children || [])]
        .map(normalize)
        .sort((a, b) => sortKey(a).localeCompare(sortKey(b))),
    });
    return [...(payload?.categories || [])].map(normalize)
      .sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
  };
  const stableOutsideCourses = payload => [...(payload?.outside_courses || [])]
    .sort((a, b) => [
      String(a?.course_code || ''), String(a?.course_name || ''), String(a?.term_code || ''),
    ].join('\u001f').localeCompare([
      String(b?.course_code || ''), String(b?.course_name || ''), String(b?.term_code || ''),
    ].join('\u001f')));
  const identityFields = [
    'student_id', 'program_code', 'program_name', 'grade', 'college', 'major',
    'class_name', 'expected_graduation',
  ];
  const identityChanged = identityFields.some(field => (
    String(beforePayload?.[field] ?? '') !== String(afterPayload?.[field] ?? '')
  ));

  if (
    !items.length
    && JSON.stringify(stableCategoryTree(beforePayload))
      !== JSON.stringify(stableCategoryTree(afterPayload))
  ) {
    items.push('培养计划的类别结构、培养要求或其他内容发生变化');
  }
  // A revision can still differ when an older cache used a different remote
  // ordering.  Do not turn that normalization-only difference into a user
  // visible update notification.
  if (
    !items.length
    && JSON.stringify(stableCategoryTree(beforePayload))
      === JSON.stringify(stableCategoryTree(afterPayload))
    && JSON.stringify(stableOutsideCourses(beforePayload))
      === JSON.stringify(stableOutsideCourses(afterPayload))
    && !identityChanged
  ) {
    return [];
  }
  if (!items.length && !courseCounts.added && !courseCounts.removed && !courseCounts.changed) {
    items.push('培养计划内容发生变化');
  }
  return items;
};

export const summarizeAcademicReportSnapshot = payload => {
  const items = [`最新版包含 ${collectAcademicCourses(payload).length} 门计划课程`];
  const summary = payload?.credit_summary || {};
  if (hasValue(summary.total_required)) items.push(`要求学分：${summary.total_required}`);
  if (hasValue(summary.total_passed)) items.push(`已修学分：${summary.total_passed}`);
  if (hasValue(summary.total_remaining)) items.push(`待修学分：${summary.total_remaining}`);
  items.push('旧基线未保留完整内容，无法逐门对比');
  return items;
};

export const summarizeResearchTrainingUpdate = (beforePayload, afterPayload) => {
  const items = [];
  if (beforePayload && afterPayload) {
    const topicId = topic => String(topic?.topic_id || topic?.id || topic?.project_id || '');
    const beforeTopics = new Map((beforePayload.topics || []).map(topic => [topicId(topic), topic]));
    const afterTopics = new Map((afterPayload.topics || []).map(topic => [topicId(topic), topic]));
    const added = [...afterTopics.keys()].filter(key => key && !beforeTopics.has(key)).length;
    const removed = [...beforeTopics.keys()].filter(key => key && !afterTopics.has(key)).length;
    const updated = [...afterTopics.entries()].filter(([key, topic]) => (
      key && beforeTopics.has(key)
      && JSON.stringify(beforeTopics.get(key)) !== JSON.stringify(topic)
    )).length;
    if (JSON.stringify(beforePayload.batch || {}) !== JSON.stringify(afterPayload.batch || {})) {
      items.push('科研训练报名批次已经更新');
    }
    if (added) items.push(`新增 ${added} 个课题`);
    if (updated) items.push(`${updated} 个课题信息有变化`);
    if (removed) items.push(`${removed} 个课题已下架`);
    if (
      JSON.stringify(beforePayload.eligibility || {})
      !== JSON.stringify(afterPayload.eligibility || {})
    ) items.push('报名资格发生变化');
    if (
      JSON.stringify(beforePayload.confirmed_topics || [])
      !== JSON.stringify(afterPayload.confirmed_topics || [])
    ) items.push('已确认课题状态发生变化');
  } else {
    const changes = (afterPayload || beforePayload)?.changes || {};
    if (changes.new_batch) items.push('科研训练报名批次已经更新');
    if (changes.added) items.push(`新增 ${changes.added} 个课题`);
    if (changes.updated) items.push(`${changes.updated} 个课题信息有变化`);
    if (changes.removed) items.push(`${changes.removed} 个课题已下架`);
    if (changes.eligibility_changed) items.push('报名资格发生变化');
    if (changes.confirmed_changed) items.push('已确认课题状态发生变化');
  }
  return items.length ? items : ['课题数据内容发生变化'];
};
