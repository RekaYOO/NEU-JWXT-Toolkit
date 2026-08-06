import {
  evaluationConfirmationText,
  evaluationStrategyLabel,
} from '../features/evaluationConfirmation';

test.each([
  ['highest', '最高分策略'],
  ['lowest', '最低分策略'],
  ['custom', '自定义策略'],
])('生成可读的评教策略名称: %s', (strategy, expected) => {
  expect(evaluationStrategyLabel(strategy)).toBe(expected);
});

test('提交确认明确显示策略和课程数量', () => {
  expect(evaluationConfirmationText('highest', 3)).toBe(
    '即将使用最高分策略对3门课提交评教。'
  );
  expect(evaluationConfirmationText('custom', 1)).toBe(
    '即将使用自定义策略对1门课提交评教。'
  );
});
