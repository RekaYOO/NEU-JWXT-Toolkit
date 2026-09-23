import { relatedSystemMessages, summarizeSystemMessages } from './systemMessageSummary';

describe('system message summaries', () => {
  const messages = [
    {
      id: 'timetable',
      title: '课程变更通知',
      content: '上课地点调整',
      sent_at: '2026-09-23 10:00:00',
      read: false,
    },
    {
      id: 'plan',
      title: '培养方案更新',
      content: '学分要求有变化',
      sent_at: '2026-09-22 10:00:00',
      read: true,
    },
    {
      id: 'other',
      title: '校园活动',
      content: '欢迎参加',
      sent_at: '2026-09-21 10:00:00',
      read: false,
    },
  ];

  test('filters timetable and academic messages independently', () => {
    expect(relatedSystemMessages(messages, 'timetable').map(item => item.id)).toEqual(['timetable']);
    expect(relatedSystemMessages(messages, 'academic').map(item => item.id)).toEqual(['plan']);
  });

  test('adds unread and fallback text without mutating source messages', () => {
    const summary = summarizeSystemMessages(messages, 'timetable');
    expect(summary).toEqual(['未读：课程变更通知（2026-09-23 10:00:00）：上课地点调整']);
    expect(messages[0].read).toBe(false);
  });

  test('recognizes page-specific categories and expires old read messages', () => {
    const now = new Date('2026-09-23T12:00:00');
    const categoryMessages = [
      { id: 'experiment', title: '实验选课安排', content: '实验班开放选课', sent_at: '2026-09-22 08:00:00', read: true },
      { id: 'exam', title: '考务管理通知', content: '考试安排已发布', sent_at: '2026-09-22 08:00:00', read: true },
      { id: 'timetable', title: '调停课通知', content: '周三补课', sent_at: '2026-09-22 08:00:00', read: true },
      { id: 'old', title: '考试通知', content: '历史通知', sent_at: '2026-01-01 08:00:00', read: true },
    ];
    expect(relatedSystemMessages(categoryMessages, 'experiment', { now }).map(item => item.id)).toEqual(['experiment']);
    expect(relatedSystemMessages(categoryMessages, 'exam', { now }).map(item => item.id)).toEqual(['exam']);
    expect(relatedSystemMessages(categoryMessages, 'timetable', { now }).map(item => item.id)).toEqual(['timetable']);
    expect(relatedSystemMessages(categoryMessages, 'exam', { now }).map(item => item.id)).not.toContain('old');
  });
});
