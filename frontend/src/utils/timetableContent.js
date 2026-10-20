// Generated identifiers and remote paint choices are not schedule changes.
const COURSE_METADATA = new Set(['id', 'meeting_id', 'color']);
const CACHE_METADATA = new Set(['cache', 'is_fresh', 'last_update', 'source', 'fetched_at']);

export const stableTimetableValue = value => {
  if (Array.isArray(value)) return value.map(stableTimetableValue)
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  if (!value || typeof value !== 'object') return value;
  return Object.keys(value).filter(key => !CACHE_METADATA.has(key)).sort()
    .reduce((result, key) => {
      result[key] = stableTimetableValue(value[key]);
      return result;
    }, {});
};

export const timetableCourseContent = course => stableTimetableValue(
  Object.fromEntries(Object.entries(course || {}).filter(([key]) => !COURSE_METADATA.has(key))),
);

export const timetableContentSignature = payload => JSON.stringify(stableTimetableValue({
  term_code: payload?.term_code || '',
  campuses: payload?.campuses || [],
  weeks: payload?.weeks || [],
  sections_by_campus: payload?.sections_by_campus || {},
  courses: (payload?.courses || []).map(timetableCourseContent),
  unscheduled: payload?.unscheduled || [],
  practices: payload?.practices || [],
}));
