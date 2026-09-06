export const LEGACY_GPA_MODE = 'through_2024';
export const MODERN_GPA_MODE = 'from_2025';
export const GPA_MODE_OPTIONS = [
  { label: '2024级及以前', value: LEGACY_GPA_MODE },
  { label: '2025级及以后', value: MODERN_GPA_MODE },
];

export const gpaExclusionReason = (course, policy = {}) => {
  if (policy?.mode !== MODERN_GPA_MODE) return '';
  const code = course.code || course.course_code;
  const generalElective = course.gpa_general_elective
    || (policy.general_elective_codes || []).includes(code)
    || [course.course_category, course.courseCategory, course.category_path, course.categoryPath,
      course.course_subcategory, course.category]
      .some(value => String(value || '').includes('通识选修'));
  if (generalElective) return '通识选修类不计入绩点';
  const scale = policy.grading_scales?.[code] || course.gradingScale || course.grading_scale;
  return String(scale || '').trim() === '两级制' ? '两级制不计入绩点' : '';
};

export const summarizeGpa = (courses, policy = {}) => {
  const included = courses.filter(course => (
    !gpaExclusionReason(course, policy)
    && course.gpa !== null && course.gpa !== undefined && course.gpa !== ''
    && Number.isFinite(Number(course.gpa)) && Number(course.gpa) >= 0
    && Number.isFinite(Number(course.credit)) && Number(course.credit) > 0
  ));
  const credits = included.reduce((sum, course) => sum + Number(course.credit), 0);
  const points = included.reduce((sum, course) => sum + Number(course.gpa) * Number(course.credit), 0);
  return { average: credits > 0 ? points / credits : null, credits, points, count: included.length };
};

export const calculateGpaImpacts = (courses, policy = {}) => {
  const { credits, points, average } = summarizeGpa(courses, policy);
  return courses.map(course => {
    if (!summarizeGpa([course], policy).count) {
      return { ...course, mean_adjust_delta: 0, exclude_delta: 0 };
    }
    const credit = Number(course.credit);
    const gpa = Number(course.gpa);
    return {
      ...course,
      mean_adjust_delta: credits > 0 ? credit * (gpa - average) / credits : 0,
      exclude_delta: credits > credit ? average - (points - credit * gpa) / (credits - credit) : null,
    };
  });
};
