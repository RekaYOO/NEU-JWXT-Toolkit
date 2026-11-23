import { saveNativeFile } from '../services/nativeBridge';

export function downloadTimetable({ termCode, mode, view, week, days, schedule }) {
  const fields = ['course_name', 'course_code', 'weekday', 'weeks', 'recurrence_unknown', 'start_section', 'end_section', 'location', 'teachers', 'details', 'agenda_date', 'start_time', 'end_time'];
  const project = course => Object.fromEntries(fields.filter(field => course[field] != null).map(field => [field, course[field]]));
  const data = {
    term_code: termCode, mode, view, week: view === 'week' ? week : null,
    courses: Object.values(days).flat().map(project),
    unscheduled: (schedule.unscheduled || []).map(project),
    practices: (schedule.practices || []).map(project),
  };
  const filename = `课表_${termCode}_${mode}_${view === 'week' ? `第${week}周` : '全学期'}.json`.replace(/[\\/:*?"<>|]/g, '_');
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  if (saveNativeFile(filename, blob.type, blob)) return;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  try {
    link.click();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
