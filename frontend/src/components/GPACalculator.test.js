import { gpaCourseGradingScale, selectGpaBusinessColumnKeys } from './GPACalculator';

jest.mock('../services/api', () => ({}));

test('GPA 业务列严格跟随成绩页的可见状态与顺序', () => {
  const scoreColumns = [
    { key: 'term_display', visible: true },
    { key: 'name', visible: true },
    { key: 'grading_scale', visible: false },
    { key: 'exam_type', visible: true },
  ];

  expect(selectGpaBusinessColumnKeys(scoreColumns, [
    'name', 'term_display', 'grading_scale', 'exam_type',
  ])).toEqual(['term_display', 'name', 'exam_type']);
});

test('GPA 不把来源和操作混入成绩页业务列映射', () => {
  expect(selectGpaBusinessColumnKeys([
    { key: 'name', visible: true },
    { key: 'source', visible: true },
    { key: 'action', visible: true },
  ], ['name'])).toEqual(['name']);
});

test('GPA 模拟器不显示成绩列，即使成绩明细启用了该列', () => {
  expect(selectGpaBusinessColumnKeys([
    { key: 'credit', visible: true }, { key: 'score', visible: true }, { key: 'gpa', visible: true },
  ], ['credit', 'score', 'gpa'])).toEqual(['credit', 'gpa']);
});

test('培养计划课程的成绩分制优先使用导入快照并兼容元数据补全', () => {
  expect(gpaCourseGradingScale({ code: 'A100', gradingScale: '等级制' }, {
    A100: { grading_scale: '百分制' },
  })).toBe('等级制');
  expect(gpaCourseGradingScale({ code: 'A100' }, {
    A100: { grading_scale: '百分制' },
  })).toBe('百分制');
  expect(gpaCourseGradingScale({ code: 'A200' }, {})).toBe('分制待定');
});
