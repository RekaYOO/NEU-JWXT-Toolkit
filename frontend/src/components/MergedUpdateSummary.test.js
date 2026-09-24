import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import MergedUpdateSummary from './MergedUpdateSummary';

test('combines the detected change and official notice in separate readable sections', () => {
  const markup = renderToStaticMarkup(
    <MergedUpdateSummary
      changes={['课程甲：地点 旧教室 → 新教室']}
      messages={['未读：课程变更通知：地点调整']}
    />,
  );
  expect(markup).toContain('课程甲：地点 旧教室 → 新教室');
  expect(markup).toContain('教务系统未读消息（供对照）');
  expect(markup).toContain('未读：课程变更通知：地点调整');
  expect(markup.indexOf('旧教室')).toBeLessThan(markup.indexOf('供对照'));
});

test('omits the official notice section when no relevant message was fetched', () => {
  const markup = renderToStaticMarkup(<MergedUpdateSummary changes={['新增课程：课程乙']} />);
  expect(markup).toContain('新增课程：课程乙');
  expect(markup).not.toContain('供对照');
});
