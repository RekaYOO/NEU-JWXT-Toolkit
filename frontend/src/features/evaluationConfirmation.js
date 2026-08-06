const EVALUATION_STRATEGY_LABELS = {
  highest: '最高分策略',
  lowest: '最低分策略',
  custom: '自定义策略',
};

export const evaluationStrategyLabel = (strategy) => (
  EVALUATION_STRATEGY_LABELS[strategy] || EVALUATION_STRATEGY_LABELS.custom
);

export const evaluationConfirmationText = (strategy, courseCount) => (
  `即将使用${evaluationStrategyLabel(strategy)}对${courseCount}门课提交评教。`
);
