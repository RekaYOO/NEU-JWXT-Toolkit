import {
  ACADEMIC_REPORT_DEFAULT_COLUMNS,
  SCORE_DEFAULT_COLUMNS,
} from './defaultColumnConfigs';

const visibleKeys = columns => columns.filter(column => column.visible).map(column => column.key);

test('培养计划默认显示学期，课程代码合并到名称列且类别路径默认隐藏', () => {
  expect(visibleKeys(ACADEMIC_REPORT_DEFAULT_COLUMNS)).toEqual([
    'course_name', 'credit', 'status', 'course_nature', 'term_code',
  ]);
  expect(ACADEMIC_REPORT_DEFAULT_COLUMNS.at(-1).key).toBe('category_path');
});

test('成绩页默认显示考核方式而不显示课程代码', () => {
  expect(visibleKeys(SCORE_DEFAULT_COLUMNS)).toEqual([
    'name', 'score', 'gpa', 'credit', 'term_display', 'course_type', 'exam_type', 'is_passed',
  ]);
});
