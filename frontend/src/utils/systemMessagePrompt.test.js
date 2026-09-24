import {
  acknowledgeSystemMessagePrompts, clearSystemMessagePromptMemory,
  unreadSystemMessagePrompts,
} from './systemMessagePrompt';

const unread = {
  id: 'm1',
  kind: 'reminder',
  title: '考试安排通知',
  content: '考场变更',
  sent_at: '2026-09-23 10:00:00',
  read: false,
};

afterEach(clearSystemMessagePromptMemory);

test('official read notifications never prompt; acknowledged messages stay quiet across page mounts', () => {
  expect(unreadSystemMessagePrompts([{ ...unread, read: true }], 'exam', 'a')).toEqual([]);
  expect(unreadSystemMessagePrompts([unread], 'exam', 'a')).toEqual([unread]);
  // A page switch before acknowledgement must not silently mark it as read.
  expect(unreadSystemMessagePrompts([unread], 'exam', 'a')).toEqual([unread]);
  acknowledgeSystemMessagePrompts('a', 'exam', [unread]);
  expect(unreadSystemMessagePrompts([unread], 'exam', 'a')).toEqual([]);
  expect(unreadSystemMessagePrompts([unread], 'exam', 'b')).toEqual([unread]);
  expect(unreadSystemMessagePrompts([{ ...unread, content: '考场再次变更' }], 'exam', 'a')).toHaveLength(1);
});

test('messages without stable official identifiers cannot be acknowledged as a different message', () => {
  expect(unreadSystemMessagePrompts([{ ...unread, id: '' }], 'exam', 'a')).toEqual([]);
});

test('newer read notifications do not crowd an older unread notice out of the prompt limit', () => {
  const alreadyRead = Array.from({ length: 8 }, (_, index) => ({
    ...unread, id: `read-${index}`, read: true,
    sent_at: `2026-09-24 10:0${index}:00`,
  }));
  expect(unreadSystemMessagePrompts([...alreadyRead, unread], 'exam', 'a')).toEqual([unread]);
});
