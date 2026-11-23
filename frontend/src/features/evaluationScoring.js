export const evaluationOptions = indicator => {
  if (!Array.isArray(indicator?.level_json)) return [];
  const seen = new Set();
  const result = [];
  for (const option of indicator.level_json) {
    const value = Number(option.value);
    const score = option.df == null ? 40 + value * 10 : Number(option.df);
    if (!Number.isInteger(value) || value < 1 || value > 6 || !Number.isFinite(score) || score < 0 || seen.has(value)) return [];
    seen.add(value);
    result.push({ value, score, label: `${option.mc || `等级 ${value}`} (${score})` });
  }
  return result.sort((left, right) => right.score - left.score || right.value - left.value);
};

export const evaluationGradeLabel = (indicator, value) => evaluationOptions(indicator)
  .find(option => option.value === Number(value))?.label || String(value ?? '-');

export const applyEvaluationStrategy = (indicators, strategy) => {
  let selectionCount = 0;
  return indicators.map(indicator => {
    if (indicator.evaltype !== 1) return indicator;
    const options = evaluationOptions(indicator);
    if (strategy === 'lowest') options.reverse();
    const choice = options[selectionCount === 0 && options.length > 1 ? 1 : 0]?.value;
    selectionCount += 1;
    return { ...indicator, _customDfdj: choice == null ? undefined : indicator.sfdx === 1 ? [choice] : choice };
  });
};

export const evaluationInputErrors = indicators => {
  const errors = [];
  const effective = [];
  if (!indicators.length) return ['尚未读取到评教指标'];
  for (const indicator of indicators) {
    if (indicator.evaltype !== 1) {
      if (indicator.sfbt === 1 && !String(indicator._customResult || '').trim()) errors.push(`必填文本指标未填写: ${indicator.zbmc}`);
      continue;
    }
    const options = evaluationOptions(indicator);
    if (!options.length) { errors.push(`官方评分选项不可用: ${indicator.zbmc}`); continue; }
    const values = indicator._customDfdj == null ? [] : [].concat(indicator._customDfdj);
    if (indicator.sfbt === 1 && !values.length) errors.push(`必填指标未评分: ${indicator.zbmc}`);
    if (indicator.sfdx !== 1 && values.length > 1) errors.push(`单选指标不能多选: ${indicator.zbmc}`);
    if (new Set(values).size !== values.length || values.some(value => !options.some(option => option.value === value))) errors.push(`请选择官方评分选项: ${indicator.zbmc}`);
    const chosen = options.find(option => values.includes(option.value));
    if (chosen) effective.push(chosen.value);
  }
  if (effective.length > 1 && new Set(effective).size === 1) errors.push('评价选项不能全部相同');
  return errors;
};
