const textOf = item => `${item?.title || ''} ${item?.content || ''}`;

const PATTERNS = {
  academic: /(培养方案|培养计划|学分|课程计划|成绩|选课结果)/,
  timetable: /(调停|调课|补课|停课|课程变更|课表|上课时间|上课地点)/,
  experiment: /(实验选课|实验课程|实验项目|实验班|实验课)/,
  exam: /(考务|考试|考场|座位|监考|考试安排)/,
};

const dateOf = value => {
  const text = String(value || '').trim();
  if (!text) return null;
  const date = new Date(text.includes('T') ? text : text.replace(' ', 'T'));
  return Number.isNaN(date.getTime()) ? null : date;
};

export const relatedSystemMessages = (
  messages,
  kind,
  { now = new Date(), maxAgeDays = 30 } = {},
) => {
  const pattern = PATTERNS[kind] || PATTERNS.academic;
  const cutoff = new Date(now.getTime() - maxAgeDays * 24 * 60 * 60 * 1000);
  return (messages || [])
    .filter(item => {
      if (!pattern.test(textOf(item))) return false;
      const sentAt = dateOf(item?.sent_at);
      return Boolean(item?.read === false || !sentAt || sentAt >= cutoff);
    })
    .sort((a, b) => (dateOf(b?.sent_at)?.getTime() || 0) - (dateOf(a?.sent_at)?.getTime() || 0))
    .slice(0, 6);
};

export const summarizeSystemMessages = (messages, kind, options) => relatedSystemMessages(messages, kind, options)
  .map(item => `${item.read ? '' : '未读：'}${item.title || '教务系统通知'}${item.sent_at ? `（${item.sent_at}）` : ''}：${item.content || '请打开系统消息查看详情'}`);
