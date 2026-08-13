import { selectGpaBusinessColumnKeys } from './GPACalculator';

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
