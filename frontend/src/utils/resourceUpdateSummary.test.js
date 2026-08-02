import {
  summarizeAcademicReportUpdate,
  summarizeAcademicReportSnapshot,
  summarizeResearchTrainingUpdate,
  summarizeScoreUpdate,
} from './resourceUpdateSummary';

test('score update summary includes course and GPA changes', () => {
  const before = {
    overall_gpa: 3.5,
    scores: [
      { code: 'A1', term: '2025-2026-1', name: '课程甲', score: '80', gpa: 3.0 },
    ],
  };
  const after = {
    overall_gpa: 3.8,
    scores: [
      { code: 'A1', term: '2025-2026-1', name: '课程甲', score: '85', gpa: 3.5 },
      { code: 'A2', term: '2025-2026-1', name: '课程乙', score: '90', gpa: 4.0 },
    ],
  };

  expect(summarizeScoreUpdate(before, after)).toEqual([
    '新增课程：课程乙',
    '课程甲：成绩 80 → 85，绩点 3 → 3.5',
    '总 GPA：3.5 → 3.8',
  ]);
});

test('score update summary preserves legacy and duplicate course rows', () => {
  const before = { scores: [
    { name: '旧数据甲', term: '2024-2025-1', credit: 2, score: '70' },
    { name: '旧数据乙', term: '2024-2025-1', credit: 2, score: '80' },
    { code: 'DUP', term: '2024-2025-1', name: '重复一', score: '60' },
    { code: 'DUP', term: '2024-2025-1', name: '重复二', score: '61' },
  ] };
  const after = { scores: [
    { name: '旧数据甲', term: '2024-2025-1', credit: 2, score: '70' },
    { name: '旧数据乙', term: '2024-2025-1', credit: 2, score: '81' },
    { code: 'DUP', term: '2024-2025-1', name: '重复一', score: '60' },
    { code: 'DUP', term: '2024-2025-1', name: '重复二', score: '62' },
  ] };

  expect(summarizeScoreUpdate(before, after)).toEqual([
    '旧数据乙：成绩 80 → 81',
    '重复二：成绩 61 → 62',
  ]);
});

test('academic report summary includes credit and course status changes', () => {
  const before = {
    credit_summary: { total_passed: 20, total_remaining: 10 },
    categories: [{
      courses: [{
        course_code: 'A1', term_code: '2025-2026-1', course_name: '课程甲',
        is_passed: false, score: '58',
      }],
    }],
  };
  const after = {
    credit_summary: { total_passed: 22, total_remaining: 8 },
    categories: [{
      courses: [{
        course_code: 'A1', term_code: '2025-2026-1', course_name: '课程甲',
        is_passed: true, score: '85',
      }],
    }],
  };

  expect(summarizeAcademicReportUpdate(before, after)).toEqual([
    '已修学分：20 → 22',
    '待修学分：10 → 8',
    '课程甲：通过状态 未通过 → 已通过，成绩 58 → 85',
  ]);
});

test('academic report snapshot is honest when the old baseline is unavailable', () => {
  const items = summarizeAcademicReportSnapshot({
    credit_summary: { total_required: 160, total_passed: 80 },
    categories: [{ courses: [
      { course_code: 'A1', term_code: '2025-2026-1', course_name: '课程甲' },
    ] }],
  });
  expect(items).toContain('最新版包含 1 门计划课程');
  expect(items).toContain('旧基线未保留完整内容，无法逐门对比');
  expect(items.some(item => item.includes('新增'))).toBe(false);
});

test('academic report summary falls back to category requirement changes', () => {
  expect(summarizeAcademicReportUpdate(
    { categories: [{ name: '旧类别' }] },
    { categories: [{ name: '新类别' }] },
  )).toEqual(['培养计划的类别结构、培养要求或其他内容发生变化']);
});

test('research training summary compares displayed and available snapshots', () => {
  const before = {
    batch: { batch_id: '1' },
    eligibility: { allowed: false },
    topics: [{ topic_id: 'A', title: '旧标题' }],
    confirmed_topics: [],
  };
  const after = {
    batch: { batch_id: '1' },
    eligibility: { allowed: true },
    topics: [
      { topic_id: 'A', title: '新标题' },
      { topic_id: 'B', title: '新增课题' },
    ],
    confirmed_topics: [{ topic_id: 'A' }],
  };

  expect(summarizeResearchTrainingUpdate(before, after)).toEqual([
    '新增 1 个课题',
    '1 个课题信息有变化',
    '报名资格发生变化',
    '已确认课题状态发生变化',
  ]);
});
