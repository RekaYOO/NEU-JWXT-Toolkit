const textOf = item => `${item?.title || ''} ${item?.content || ''}`;

export const relatedSystemMessages = (messages, kind) => {
  const patterns = kind === 'timetable'
    ? /(调停|调课|停课|课程变更|课表|考试|考场|上课时间|上课地点)/
    : /(培养方案|培养计划|学分|课程计划|成绩|选课结果|实验选课)/;
  return (messages || [])
    .filter(item => patterns.test(textOf(item)))
    .slice(0, 6);
};

export const summarizeSystemMessages = (messages, kind) => relatedSystemMessages(messages, kind)
  .map(item => `${item.read ? '' : '未读：'}${item.title || '教务系统通知'}${item.sent_at ? `（${item.sent_at}）` : ''}：${item.content || '请打开系统消息查看详情'}`);
