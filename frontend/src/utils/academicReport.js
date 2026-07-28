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
