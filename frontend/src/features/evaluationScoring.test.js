import { evaluationOptions, applyEvaluationStrategy, evaluationInputErrors } from './evaluationScoring';

const indicator = (zbid, extra = {}) => ({
  zbid, zbmc: zbid, evaltype: 1, sfbt: 1,
  level_json: [{ value: 2, df: 32, mc: '需改进' }, { value: 5, df: 85, mc: '符合要求' }],
  ...extra,
});

test('uses only official options and preserves custom wording and scores', () => {
  expect(evaluationOptions(indicator('first'))).toEqual([
    { value: 5, score: 85, label: '符合要求 (85)' },
    { value: 2, score: 32, label: '需改进 (32)' },
  ]);
  expect(evaluationOptions({ level_json: [{ value: 8 }] })).toEqual([]);
});

test('strategy preserves written feedback and chooses legal distinct effective grades', () => {
  const text = { zbid: 'text', evaltype: 2, _customResult: '保留评价' };
  const result = applyEvaluationStrategy([indicator('first'), indicator('second', { sfdx: 1 }), text], 'highest');
  expect(result.map(item => item._customDfdj)).toEqual([2, [5], undefined]);
  expect(result[2]).toEqual(text);
  expect(evaluationInputErrors(result)).toEqual([]);
});

test('validates effective multi-select scores and required empty arrays', () => {
  expect(evaluationInputErrors([
    indicator('first', { _customDfdj: 5 }),
    indicator('second', { sfdx: 1, _customDfdj: [2, 5] }),
  ])).toContain('评价选项不能全部相同');
  expect(evaluationInputErrors([indicator('first', { sfdx: 1, _customDfdj: [] })])).toContain('必填指标未评分: first');
  expect(evaluationInputErrors([indicator('first', { _customDfdj: 6 })])).toContain('请选择官方评分选项: first');
  expect(evaluationInputErrors([])).toContain('尚未读取到评教指标');
});
