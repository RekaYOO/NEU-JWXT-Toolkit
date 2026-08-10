import { buildCourseOutlineHtml } from './courseOutlineExport';

test('course outline export escapes untrusted remote content', () => {
  const html = buildCourseOutlineHtml({
    overview: { course_code: 'A100', course_name: '<script>alert(1)</script>', introduction: '简介' },
    groups: { teaching: { sections: [{ kind: 'info', title: '课程目标', items: [[{ label: '目标', value: '<img src=x onerror=alert(1)>' }]] }] }, assessment: {}, governance: {} },
  });
  expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
  expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  expect(html).not.toContain('<script>alert(1)</script>');
});

test('course outline export only renders the semantic presentation model', () => {
  const html = buildCourseOutlineHtml({
    overview: { course_code: 'A100', course_name: '测试课程', assessment_method: '考试' },
    groups: { teaching: { course_code: 'A100', sections: [{ kind: 'info', title: '课程目标', items: [[{ label: '课程目标', value: '掌握基础知识' }]] }] }, assessment: {}, governance: {} },
  });
  expect(html).toContain('掌握基础知识');
  expect(html).not.toContain('course_code');
  expect(html).not.toContain('sections');
  expect(html).not.toContain('kind');
});
