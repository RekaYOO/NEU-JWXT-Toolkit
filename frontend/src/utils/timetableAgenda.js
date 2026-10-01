import dayjs from 'dayjs';

const DAYS = [7, 1, 2, 3, 4, 5, 6];
// Manual agenda entries use the same color-mix pipeline as official courses,
// but start from a muted slate-blue so they do not fall back to vivid brand blue.
const MANUAL_AGENDA_COLOR = '#94a3b8';
export const agendaDateForDay = (weeks, week, day) => {
  const row = (weeks || []).find(item => Number(item.number) === Number(week));
  const offset = DAYS.indexOf(Number(day));
  if (!row?.start_date || offset < 0) return null;
  const value = String(row.start_date).slice(0, 10);
  const start = dayjs(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !start.isValid() || start.format('YYYY-MM-DD') !== value) return null;
  return start.add(offset, 'day').format('YYYY-MM-DD');
};

export const agendaDayContext = (weeks, date) => {
  for (const week of weeks || []) {
    for (const day of DAYS) {
      if (agendaDateForDay(weeks, week.number, day) === date) return { week: Number(week.number), day };
    }
  }
  return null;
};

export const agendaEventDates = (event, weeks = []) => {
  if (event?.selection_mode === 'week' || event?.mode === 'week') {
    const weekday = Number(event.weekday);
    if (!Number.isInteger(weekday)) return [];
    return (weeks || [])
      .filter(week => (event.weeks || []).map(Number).includes(Number(week.number)))
      .map(week => agendaDateForDay(weeks, week.number, weekday))
      .filter(Boolean);
  }
  return [...new Set((event?.dates || (event?.date ? [event.date] : []))
    .map(value => String(value).slice(0, 10))
    .filter(Boolean))];
};

export const agendaEventOccursOnDate = (event, weeks, date) => (
  agendaEventDates(event, weeks).includes(String(date).slice(0, 10))
);

export const agendaEventMatchesDate = (event, weeks, week, day) => {
  if (week == null || day == null) return false;
  const date = agendaDateForDay(weeks, week, day);
  return Boolean(date && agendaEventOccursOnDate(event, weeks, date));
};

export const agendaCourseSortKey = course => {
  const time = String(course?.start_time || '').match(/^(\d{1,2}):(\d{2})/);
  const minutes = time ? Number(time[1]) * 60 + Number(time[2]) : Number.POSITIVE_INFINITY;
  const section = Number(course?.start_section);
  return {
    minutes,
    section: Number.isFinite(section) && section > 0 ? section : Number.POSITIVE_INFINITY,
    id: String(course?.id || ''),
  };
};

export const sortAgendaCourses = courses => [...(courses || [])].sort((left, right) => {
  const a = agendaCourseSortKey(left);
  const b = agendaCourseSortKey(right);
  return a.minutes - b.minutes || a.section - b.section || a.id.localeCompare(b.id);
});

export const agendaCoursesOnDate = (courses, weeks, date) => {
  const context = agendaDayContext(weeks, date);
  if (!context) return [];
  return (courses || []).filter(course => Number(course.weekday) === context.day
    && (course.weeks || []).map(Number).includes(context.week));
};

export const agendaSemesterEnded = (document, weeks, now) => {
  if (!document) return false;
  if (document?.semester_ended) return true;
  if (document?.semester_end && now) return document.semester_end < dayjs(now).format('YYYY-MM-DD');
  if (!now || !weeks?.length) return false;
  const rows = [...weeks].sort((a, b) => Number(a.number) - Number(b.number));
  let previous;
  for (let index = 0; index < rows.length; index += 1) {
    const start = agendaDateForDay(rows, rows[index].number, 7);
    const end = agendaDateForDay(rows, rows[index].number, 6);
    if (Number(rows[index].number) !== index + 1 || !start || dayjs(start).day() !== 0
      || rows[index].end_date !== end || (previous && dayjs(previous).add(1, 'day').format('YYYY-MM-DD') !== start)) return false;
    previous = end;
  }
  return previous < dayjs(now).format('YYYY-MM-DD');
};

export const injectTimetableAgenda = ({ courses = [], weeks = [], document, sections = [], now }) => {
  // Rebuild only from official input, even if a caller passes a previous view.
  const original = courses.filter(course => !course.agenda_source && !course.agenda_event_id);
  const projected = original.map(course => ({ ...course, weeks: [...(course.weeks || [])] }));
  const ended = agendaSemesterEnded(document, weeks, now);
  const active = ended ? null : document;
  const ids = new Set();
  const originalIds = new Map();
  original.forEach(course => originalIds.set(course.id, (originalIds.get(course.id) || 0) + 1));
  for (const move of active?.moves || []) {
    const source = agendaDayContext(weeks, move.source);
    const target = agendaDayContext(weeks, move.target);
    if (!source || !target) continue;
    for (const [index, course] of original.entries()) {
      if (Number(course.weekday) !== source.day || !(course.weeks || []).map(Number).includes(source.week)) continue;
      const identity = course.id && originalIds.get(course.id) === 1 ? course.id : `${course.id || 'course'}-${index}`;
      const id = `agenda-move-${identity}-${move.source}-${move.target}`;
      if (ids.has(id)) continue;
      ids.add(id);
      projected.push({
        ...course, id,
        weekday: target.day, weeks: [target.week], agenda_source: move.source,
        tags: [...(course.tags || []), '调休'],
      });
    }
  }
  const events = (active?.events || []).flatMap(event => agendaEventCourses(event, weeks, sections));
  return { courses: projected, events, ended };
};

export const projectAgendaCourses = (courses, weeks, document) => injectTimetableAgenda({ courses, weeks, document }).courses;

export const agendaEventCourse = (event, weeks, sections, dateOverride) => {
  const date = dateOverride || agendaEventDates(event, weeks)[0];
  const context = agendaDayContext(weeks, date);
  if (!context) return null;
  const overlap = (sections || []).filter(section => section.start_time && section.end_time
    && event.start_time < section.end_time && event.end_time > section.start_time);
  return {
    id: `agenda-event-${event.id}-${date}`, agenda_event_id: event.id, agenda_date: date,
    agenda_selection_mode: event.selection_mode || 'date',
    course_name: event.title, location: event.location, teachers: event.note ? [event.note] : [],
    course_nature: event.important, weekday: context.day, weeks: [context.week],
    start_time: event.start_time, end_time: event.end_time,
    start_section: overlap[0]?.number || null, end_section: overlap[overlap.length - 1]?.number || null,
    tags: ['日程'], agenda_kind: 'manual', color: MANUAL_AGENDA_COLOR,
  };
};

export const agendaEventCourses = (event, weeks, sections) => (
  agendaEventDates(event, weeks)
    .map(date => agendaEventCourse(event, weeks, sections, date))
    .filter(Boolean)
);
