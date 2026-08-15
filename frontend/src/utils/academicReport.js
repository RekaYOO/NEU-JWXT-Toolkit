const VALID_REQUIREMENT_TYPES = new Set(['required', 'elective', 'mixed']);

export const getCategoryRequirementType = (node) => {
  if (VALID_REQUIREMENT_TYPES.has(node?.requirement_type)) {
    return node.requirement_type;
  }

  // 兼容升级前已经缓存在本地的培养计划数据。
  const path = Array.isArray(node?.path_array) ? node.path_array.join(' > ') : '';
  if (path.includes('选修')) return 'elective';
  if (path.includes('必修') || path.includes('实践类')) return 'required';

  const courseTypes = new Set(
    (node?.courses || [])
      .map(course => course.course_nature)
      .filter(Boolean)
  );
  if (courseTypes.size === 1 && courseTypes.has('选修')) return 'elective';
  if (courseTypes.size === 1 && courseTypes.has('必修')) return 'required';
  if (courseTypes.size > 1) return 'mixed';
  return 'unknown';
};

export const isElectiveCategory = (node) =>
  getCategoryRequirementType(node) === 'elective';

export const isRequiredCategory = (node) =>
  getCategoryRequirementType(node) === 'required';

const formatRuleNumber = (value) => {
  const number = Number(value || 0);
  return Number.isInteger(number) ? String(number) : String(Number(number.toFixed(2)));
};

/**
 * 将教务系统的综合完成状态翻译成用户可以采取行动的原因。
 *
 * 培养计划会把“已选课”计入已获得学分，但课程组的 passed 只有在课程
 * 真正通过后才会变为 true。因此 remaining_credits 为 0 并不一定代表规则
 * 已完成，此时应明确提示待通过课程，而不是伪造一个学分差额。
 */
export const getAcademicRuleDeficitText = (category) => {
  const deficits = [];
  if (Number(category?.remaining_credits || 0) > 0) {
    deficits.push(`${formatRuleNumber(category.remaining_credits)} 学分`);
  }
  if (Number(category?.missing_course_count || 0) > 0) {
    deficits.push(`${formatRuleNumber(category.missing_course_count)} 门课程`);
  }
  if (Number(category?.missing_group_count || 0) > 0) {
    deficits.push(`${formatRuleNumber(category.missing_group_count)} 个类别`);
  }
  if (deficits.length > 0) {
    return `还差 ${deficits.join('、')}`;
  }

  const pendingCourseCount = Number(category?.pending_course_count || 0);
  const pendingCredits = Number(category?.pending_credits || 0);
  if (pendingCourseCount > 0) {
    const creditText = pendingCredits > 0
      ? `（${formatRuleNumber(pendingCredits)} 学分）`
      : '';
    return `学分已选够，仍有 ${formatRuleNumber(pendingCourseCount)} 门${creditText}待通过`;
  }

  return '学分要求已满足，教务系统判定另有规则未满足';
};

const categoryDisplayName = node => {
  if (node?.name !== '选修' && node?.name !== '必修') return node?.name || '未命名类别';
  if (Array.isArray(node?.path_array) && node.path_array.length >= 2) {
    return node.path_array[node.path_array.length - 2];
  }
  return node?.name || '未命名类别';
};

const descendantCourses = node => [
  ...(node?.courses || []),
  ...(node?.children || []).flatMap(descendantCourses),
];

const normalizedCourseKey = value => String(value || '').trim().replace(/\s+/g, '').toLocaleLowerCase();
const normalizedCategoryKey = value => normalizedCourseKey(value)
  .replace(/课程/g, '')
  .replace(/课$/g, '')
  .replace(/类$/g, '')
  .replace(/模块$/g, '');
const numericCredit = value => {
  const parsed = Number.parseFloat(String(value ?? '').replace(/[^\d.-]/g, ''));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
};

/**
 * 在不修改 academic-report 缓存的前提下，把另一条实时数据链路中已经确认选中的课程
 * 投影到培养计划树。优先按课程代码匹配，只有代码不在计划中时才使用官方课程类别兜底；
 * 无法可靠归类的数据保留为 unmatched，禁止猜测后直接扣减缺口。
 */
export const overlayExternalSelectedCourses = (categories = [], selectedCourses = []) => {
  const cloneNode = node => ({
    ...node,
    path_array: [...(node.path_array || [])],
    courses: (node.courses || []).map(course => ({
      ...course,
      category_path: [...(course.category_path || [])],
    })),
    children: (node.children || []).map(cloneNode),
  });
  const cloned = (categories || []).map(cloneNode);
  const nodes = [];
  const courseMatches = new Map();
  const visit = (list, depth = 0) => (list || []).forEach(node => {
    const entry = { node, depth };
    nodes.push(entry);
    (node.courses || []).forEach(course => {
      const code = normalizedCourseKey(course.course_code);
      const name = normalizedCourseKey(course.course_name);
      if (code) courseMatches.set(`code:${code}`, { entry, course });
      if (name && !courseMatches.has(`name:${name}`)) {
        courseMatches.set(`name:${name}`, { entry, course });
      }
    });
    visit(node.children, depth + 1);
  });
  visit(cloned);

  const deduplicated = new Map();
  (selectedCourses || []).forEach(course => {
    const code = normalizedCourseKey(course.course_code);
    const name = normalizedCourseKey(course.course_name);
    const identity = code ? `code:${code}` : name ? `name:${name}` : '';
    if (identity && !deduplicated.has(identity)) deduplicated.set(identity, course);
  });

  const matched = [];
  const unmatched = [];
  deduplicated.forEach((selected, identity) => {
    const exact = courseMatches.get(identity)
      || (!identity.startsWith('name:') && courseMatches.get(`name:${normalizedCourseKey(selected.course_name)}`));
    if (exact?.course?.is_passed || exact?.course?.is_selected) return;

    let target = exact?.entry;
    if (!target) {
      const categoryKey = normalizedCategoryKey(selected.course_category);
      const selectedNature = normalizedCourseKey(selected.course_nature);
      const candidates = categoryKey ? nodes.filter(({ node }) => {
        const categoryNames = [
          node.name,
          node.category_name,
          ...(node.path_array || []),
        ].map(normalizedCategoryKey).filter(Boolean);
        if (!categoryNames.includes(categoryKey)) return false;
        const requirement = getCategoryRequirementType(node);
        if (selectedNature.includes('选修') && requirement === 'required') return false;
        if (selectedNature.includes('必修') && requirement === 'elective') return false;
        return true;
      }).sort((left, right) => (
        Number(Boolean(right.node.is_leaf)) - Number(Boolean(left.node.is_leaf))
        || right.depth - left.depth
      )) : [];
      [target] = candidates;
    }

    const credit = numericCredit(selected.credits) || numericCredit(exact?.course?.credit);
    if (!target || !credit) {
      unmatched.push(selected);
      return;
    }

    const projectedCourse = exact?.course || {
      course_code: selected.course_code || '',
      course_name: selected.course_name || '本轮已选课程',
      course_nature: selected.course_nature || '',
      credit,
      category_name: target.node.name,
      category_path: target.node.path_array || [],
    };
    projectedCourse.credit = numericCredit(projectedCourse.credit) || credit;
    projectedCourse.is_selected = true;
    projectedCourse.is_planned = false;
    projectedCourse.status = '已选课';
    projectedCourse.status_display = '已选课';
    projectedCourse.selection_source = 'course_selection_realtime';
    if (!exact) target.node.courses = [...(target.node.courses || []), projectedCourse];
    target.node.__externalSelections = [
      ...(target.node.__externalSelections || []),
      { ...selected, credit, course_name: projectedCourse.course_name },
    ];
    matched.push({ ...selected, credit, category_path: target.node.path || target.node.name });
  });

  const adjust = node => {
    const childAdjustments = (node.children || []).map(adjust);
    const directSelections = node.__externalSelections || [];
    const directCredits = directSelections.reduce((sum, course) => sum + numericCredit(course.credit), 0);
    const directCount = directSelections.length;
    const childRawCredits = childAdjustments.reduce((sum, item) => sum + item.rawCredits, 0);
    const childEffectiveCredits = childAdjustments.reduce((sum, item) => sum + item.effectiveForParent, 0);
    const rawCredits = directCredits + childRawCredits;
    const rawCount = directCount + childAdjustments.reduce((sum, item) => sum + item.rawCount, 0);
    const originalRemaining = Number(node.remaining_credits || 0);
    const nodeCreditDelta = node.requires_child_minimums_and_total
      ? childRawCredits
      : directCredits + childEffectiveCredits;
    const appliedCredits = Math.min(originalRemaining, nodeCreditDelta);

    node.selected_credits = Number(node.selected_credits || 0) + appliedCredits;
    node.earned_credits = Number(node.earned_credits || 0) + appliedCredits;
    node.remaining_credits = Math.max(0, originalRemaining - nodeCreditDelta);
    if (node.requires_child_minimums_and_total) {
      node.aggregate_remaining_credits = Math.max(
        0,
        Number(node.aggregate_remaining_credits || 0) - childRawCredits,
      );
    }
    node.missing_course_count = Math.max(0, Number(node.missing_course_count || 0) - rawCount);
    const newlyCoveredGroups = childAdjustments.filter(item => item.wasEmpty && item.rawCredits > 0).length;
    node.missing_group_count = Math.max(
      0,
      Number(node.missing_group_count || 0) - newlyCoveredGroups,
    );
    node.external_selected_credits = rawCredits;
    node.external_selected_count = rawCount;
    node.external_selected_courses = [
      ...directSelections,
      ...childAdjustments.flatMap(item => item.courses),
    ];
    delete node.__externalSelections;

    return {
      rawCredits,
      rawCount,
      courses: node.external_selected_courses,
      wasEmpty: Number(node.earned_credits || 0) - appliedCredits <= 0,
      effectiveForParent: Number(node.required_credits || 0) > 0
        ? Math.min(nodeCreditDelta, originalRemaining)
        : 0,
    };
  };
  cloned.forEach(adjust);
  return { categories: cloned, matched, unmatched };
};

/**
 * 使用与培养计划“缺学分”面板相同的口径生成可操作缺口。
 * earned/remaining 已把已选课计入，不能在选课工作台再次相加。
 */
export const collectAcademicPlanDeficits = (categories, filterFn = () => true) => {
  const toSummaryItem = (node, remainingCredits) => {
    const allNodeCourses = descendantCourses(node);
    const pendingCourses = allNodeCourses.filter(course => course.is_selected && !course.is_passed);
    const unfinishedCourses = allNodeCourses.filter(course => !course.is_passed && !course.is_selected);
    return {
      wid: node.wid,
      name: categoryDisplayName(node),
      originalName: node.name,
      path: node.path,
      path_array: node.path_array,
      requirement_type: getCategoryRequirementType(node),
      required_credits: node.required_credits,
      earned_credits: node.earned_credits,
      remaining_credits: remainingCredits,
      missing_course_count: node.missing_course_count || 0,
      missing_group_count: node.missing_group_count || 0,
      pending_course_count: pendingCourses.length,
      pending_credits: pendingCourses.reduce((sum, course) => sum + Number(course.credit || 0), 0),
      external_selected_credits: Number(node.external_selected_credits || 0),
      external_selected_count: Number(node.external_selected_count || 0),
      course_natures: [...new Set(allNodeCourses.map(course => course.course_nature).filter(Boolean))],
      unfinished_courses: unfinishedCourses.map(course => ({
        course_code: course.course_code,
        course_name: course.course_name,
        credit: course.credit,
        course_nature: course.course_nature,
      })),
      is_completed: node.is_completed,
    };
  };

  const collect = (nodes, parentNode = null) => {
    const result = [];
    (nodes || []).forEach(node => {
      const childItems = node.children ? collect(node.children, node) : [];
      const isDirectDoubleConstraintChild = Boolean(parentNode?.requires_child_minimums_and_total);
      if (!filterFn(node) && !isDirectDoubleConstraintChild) {
        result.push(...childItems);
        return;
      }
      if (node.required_credits <= 0) {
        result.push(...childItems);
        return;
      }
      const childrenAllZero = node.children && node.children.every(child => (
        child.required_credits === 0 && (!child.children || child.children.length === 0)
      ));
      const hasCountRuleDeficit = (node.missing_course_count || 0) > 0
        || (node.missing_group_count || 0) > 0;
      const childCreditDeficit = childItems.reduce(
        (sum, item) => sum + (item.remaining_credits || 0),
        0,
      );
      const isDoubleConstraintLevel = node.requires_child_minimums_and_total
        || isDirectDoubleConstraintChild;
      if (isDoubleConstraintLevel) {
        const ownCreditDeficit = node.requires_child_minimums_and_total
          ? (node.aggregate_remaining_credits || 0)
          : (node.remaining_credits || 0);
        const incrementalDeficit = Math.max(0, ownCreditDeficit - childCreditDeficit);
        if (incrementalDeficit > 0 || hasCountRuleDeficit) {
          result.push(toSummaryItem(node, incrementalDeficit));
        }
        result.push(...childItems);
        return;
      }
      const shouldShowParentRule = (node.remaining_credits || 0) === 0 && hasCountRuleDeficit;
      if (!node.children || node.children.length === 0 || childrenAllZero || shouldShowParentRule) {
        if (node.remaining_credits > 0 || hasCountRuleDeficit) {
          result.push(toSummaryItem(node, node.remaining_credits || 0));
        }
      }
      result.push(...childItems);
    });
    return result;
  };

  const requirementRank = { required: 0, elective: 1, mixed: 2, unknown: 3 };
  return collect(categories)
    .map((item, index) => ({ item, index }))
    .sort((left, right) => (
      (requirementRank[left.item.requirement_type] ?? 3)
      - (requirementRank[right.item.requirement_type] ?? 3)
      || left.index - right.index
    ))
    .map(({ item }) => item);
};

const approximateTextWidth = (value) => Array.from(String(value ?? '')).reduce(
  (width, character) => width + (/^[\u0000-\u00ff]$/.test(character) ? 7.5 : 14),
  0
);

/**
 * 根据表头和实际显示内容计算表格列宽。调用方可以传入 Canvas/DOM 测量函数；
 * 测量环境不可用时退回中英文字符宽度估算，保证测试和旧浏览器仍可工作。
 */
export const calculateContentAwareColumnWidths = (
  columns,
  rows,
  getDisplayValue,
  measureText = approximateTextWidth,
) => columns.map(column => {
  const values = [
    column.title,
    ...(rows || []).flatMap(row => {
      const value = getDisplayValue(column.key, row);
      return Array.isArray(value) ? value : [value];
    }),
  ];
  const contentWidth = values.reduce(
    (maximum, value) => Math.max(maximum, measureText(value)),
    0,
  );
  // 预留单元格左右 padding、排序/筛选图标和标签/按钮内边距，避免测得的
  // 纯文本宽度刚好够用时，真实组件仍挤压或覆盖相邻列。
  const controlWidth = column.hasControls ? 72 : 40;
  const configuredMinimum = Number(column.width) || 80;
  return {
    ...column,
    width: Math.ceil(Math.max(configuredMinimum, contentWidth + controlWidth)),
  };
});
