export const ACADEMIC_REPORT_DEFAULT_COLUMNS = [
  { key: 'course_name', title: '课程名称', visible: true, width: 240 },
  { key: 'course_code', title: '课程代码', visible: false, width: 120 },
  { key: 'credit', title: '学分', visible: true, width: 70 },
  { key: 'status', title: '状态', visible: true, width: 100 },
  { key: 'score', title: '成绩', visible: false, width: 80 },
  { key: 'course_nature', title: '性质', visible: true, width: 80 },
  { key: 'is_passed', title: '通过', visible: false, width: 80 },
  { key: 'term_code', title: '学期', visible: true, width: 130 },
  { key: 'is_core', title: '核心课', visible: false, width: 80 },
  { key: 'assessment_method', title: '考核方式', visible: true, width: 100 },
  { key: 'grading_scale', title: '成绩分制', visible: true, width: 100 },
  { key: 'category_path', title: '类别路径', visible: false, width: 200 },
];

export const SCORE_DEFAULT_COLUMNS = [
  { key: 'name', title: '课程名称', visible: true, width: 200 },
  { key: 'code', title: '课程代码', visible: false, width: 120 },
  { key: 'score', title: '成绩', visible: true, width: 80 },
  { key: 'gpa', title: '绩点', visible: true, width: 80 },
  { key: 'credit', title: '学分', visible: true, width: 80 },
  { key: 'term_display', title: '学期', visible: true, width: 180 },
  { key: 'course_type', title: '课程性质', visible: true, width: 100 },
  { key: 'course_category', title: '课程类别', visible: false, width: 150 },
  { key: 'general_category', title: '通识类别', visible: false, width: 150 },
  { key: 'exam_type', title: '考核方式', visible: true, width: 100 },
  { key: 'grading_scale', title: '成绩分制', visible: false, width: 100 },
  { key: 'exam_status', title: '考试状态', visible: false, width: 100 },
  { key: 'is_passed', title: '状态', visible: true, width: 80 },
  { key: 'mean_adjust_delta', title: '均分贡献', visible: false, width: 100 },
  { key: 'exclude_delta', title: '保留贡献', visible: false, width: 100 },
];

export const cloneDefaultColumns = columns => JSON.parse(JSON.stringify(columns));
