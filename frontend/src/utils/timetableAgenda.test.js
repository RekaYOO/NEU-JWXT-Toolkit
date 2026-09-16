import { agendaDateForDay, agendaDayContext, agendaCoursesOnDate, projectAgendaCourses, agendaEventCourse, injectTimetableAgenda, agendaSemesterEnded } from './timetableAgenda';

const weeks = [{ number: 2, start_date: '2026-09-13', end_date: '2026-09-19' }, { number: 3, start_date: '2026-09-20', end_date: '2026-09-26' }];
const courses = [{ id: 'a', course_name: '课程', weekday: 7, weeks: [2, 3], start_section: 1, end_section: 2, teachers: ['教师'] }];

test('maps Sunday-first teaching weeks to real calendar dates', () => {
  expect(agendaDateForDay(weeks, 2, 7)).toBe('2026-09-13');
  expect(agendaDateForDay(weeks, 2, 4)).toBe('2026-09-17');
  expect(agendaDateForDay(weeks, 2, 6)).toBe('2026-09-19');
  expect(agendaDayContext(weeks, '2026-09-20')).toEqual({ week: 3, day: 7 });
  expect(agendaDateForDay([{ number: 2 }], 2, 1)).toBeNull();
});

test('copies only the selected occurrence and never removes or changes the source', () => {
  const before = JSON.stringify(courses);
  const result = projectAgendaCourses(courses, weeks, { moves: [{ source: '2026-09-20', target: '2026-09-17' }] });
  expect(agendaCoursesOnDate(result, weeks, '2026-09-17')).toHaveLength(1);
  expect(agendaCoursesOnDate(result, weeks, '2026-09-20')).toHaveLength(1);
  expect(agendaCoursesOnDate(result, weeks, '2026-09-13')).toHaveLength(1);
  expect(JSON.stringify(courses)).toBe(before);
  expect(result[1]).toEqual(expect.objectContaining({ weekday: 4, weeks: [2], agenda_source: '2026-09-20', teachers: ['教师'] }));
  expect(projectAgendaCourses(courses, weeks, { moves: [] })).toEqual(courses);
});

test('keeps target courses and custom events independent, and copies only official courses', () => {
  const all = [...courses, { id: 'target', weekday: 4, weeks: [2] }];
  const result = projectAgendaCourses(all, weeks, { moves: [
    { source: '2026-09-20', target: '2026-09-17' },
    { source: '2026-09-17', target: '2026-09-18' },
  ] });
  expect(agendaCoursesOnDate(result, weeks, '2026-09-17')).toHaveLength(2);
  expect(agendaCoursesOnDate(result, weeks, '2026-09-18').map(course => course.id)).toEqual(['agenda-move-target-2026-09-17-2026-09-18']);
});

test('places custom events by overlapping section times and retains out-of-section events', () => {
  const event = { id: 'e', date: '2026-09-17', title: '会议', start_time: '08:10', end_time: '09:10', note: '材料', important: '重要', location: '101' };
  expect(agendaEventCourse(event, weeks, [{ number: 1, start_time: '08:00', end_time: '08:45' }, { number: 2, start_time: '08:55', end_time: '09:40' }]))
    .toEqual(expect.objectContaining({ start_section: 1, end_section: 2, course_name: '会议', teachers: ['材料'], course_nature: '重要', weekday: 4, weeks: [2] }));
  expect(agendaEventCourse({ ...event, start_time: '23:00', end_time: '23:30' }, weeks, [])?.start_section).toBeNull();
  expect(agendaEventCourse({ ...event, date: '2027-01-01' }, weeks, [])).toBeNull();
});

test('rebuilds an injected view without duplication, recursion, or cache mutation', () => {
  const official = Object.freeze(courses.map(course => Object.freeze({ ...course, weeks: Object.freeze([...course.weeks]) })));
  const document = { events: [{ id: 'e', date: '2026-09-17', title: '会议', start_time: '08:00', end_time: '09:00' }],
    moves: [{ source: '2026-09-20', target: '2026-09-17' }] };
  const first = injectTimetableAgenda({ courses: official, weeks, document });
  const again = injectTimetableAgenda({ courses: [...first.courses, ...first.events], weeks, document });
  expect(again).toEqual(first);
  expect(injectTimetableAgenda({ courses: official.map(course => ({ ...course, location: '新教室' })), weeks, document }).courses[1].location).toBe('新教室');
  expect(official[0].weeks).toEqual([2, 3]);
  expect(injectTimetableAgenda({ courses: first.courses, weeks, document: { events: [], moves: [] } }).courses).toEqual(courses);
});

test('stops injection after a confirmed semester ends, not from missing or partial calendars', () => {
  const complete = [{ number: 1, start_date: '2026-09-06', end_date: '2026-09-12' }, ...weeks];
  const document = { events: [{ id: 'e', date: '2026-09-17', title: '会议', start_time: '08:00', end_time: '09:00' }],
    moves: [{ source: '2026-09-20', target: '2026-09-17' }] };
  expect(agendaSemesterEnded(document, complete, new Date('2026-09-26T23:59:00'))).toBe(false);
  const expired = injectTimetableAgenda({ courses, weeks: complete, document, now: new Date('2026-09-27T00:01:00') });
  expect(expired).toEqual({ courses, events: [], ended: true });
  expect(agendaSemesterEnded(document, weeks, new Date('2026-10-01'))).toBe(false);
  expect(agendaSemesterEnded(document, [], new Date('2026-10-01'))).toBe(false);
  expect(agendaSemesterEnded({ semester_ended: true }, [], new Date('2026-09-16'))).toBe(true);
  expect(agendaSemesterEnded({ semester_end: '2026-10-10' }, complete, new Date('2026-09-27'))).toBe(false);
  expect(agendaSemesterEnded({ semester_end: '2026-09-26' }, [], new Date('2026-09-27'))).toBe(true);
  expect(agendaSemesterEnded(null, complete, new Date('2026-10-01'))).toBe(false);
});

test('copy identities retain distinct official meetings even when IDs are absent or repeated', () => {
  const input = [{ ...courses[0] }, { ...courses[0], start_section: 3, end_section: 4 }, { ...courses[0], id: undefined }];
  const document = { moves: [{ source: '2026-09-20', target: '2026-09-17' }] };
  const projected = injectTimetableAgenda({ courses: input, weeks, document }).courses.filter(course => course.agenda_source);
  expect(projected).toHaveLength(3);
  expect(new Set(projected.map(course => course.id)).size).toBe(3);
});
