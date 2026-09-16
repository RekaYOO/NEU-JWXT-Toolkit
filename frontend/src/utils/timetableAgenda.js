import dayjs from 'dayjs';

const DAYS = [7, 1, 2, 3, 4, 5, 6];
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
  const events = (active?.events || []).map(event => agendaEventCourse(event, weeks, sections)).filter(Boolean);
  return { courses: projected, events, ended };
};

export const projectAgendaCourses = (courses, weeks, document) => injectTimetableAgenda({ courses, weeks, document }).courses;

export const agendaEventCourse = (event, weeks, sections) => {
  const context = agendaDayContext(weeks, event.date);
  if (!context) return null;
  const overlap = (sections || []).filter(section => section.start_time && section.end_time
    && event.start_time < section.end_time && event.end_time > section.start_time);
  return {
    id: `agenda-event-${event.id}`, agenda_event_id: event.id, agenda_date: event.date,
    course_name: event.title, location: event.location, teachers: event.note ? [event.note] : [],
    course_nature: event.important, weekday: context.day, weeks: [context.week],
    start_time: event.start_time, end_time: event.end_time,
    start_section: overlap[0]?.number || null, end_section: overlap[overlap.length - 1]?.number || null,
    tags: ['日程'],
  };
};
