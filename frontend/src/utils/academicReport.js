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
