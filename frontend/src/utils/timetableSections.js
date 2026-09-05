export const TIMETABLE_CAMPUS_SCHEDULE_VERSION = 'neu-campus-schedule-v1';

const COMMON_AFTERNOON_AND_EVENING = [
  ['14:00', '14:45'],
  ['14:55', '15:40'],
  ['16:00', '16:45'],
  ['16:55', '17:40'],
  ['18:30', '19:15'],
  ['19:25', '20:10'],
  ['20:20', '21:05'],
  ['21:15', '22:00'],
];

const EARLY_MORNING = [
  ['08:00', '08:45'],
  ['08:55', '09:40'],
  ['10:00', '10:45'],
  ['10:55', '11:40'],
];

const HUNNAN_MORNING = [
  ['08:30', '09:15'],
  ['09:25', '10:10'],
  ['10:30', '11:15'],
  ['11:25', '12:10'],
];

const makeSchedule = rows => Object.freeze(rows.map(([startTime, endTime], index) => Object.freeze({
  number: index + 1,
  start_time: startTime,
  end_time: endTime,
})));

const EARLY_SCHEDULE = makeSchedule([...EARLY_MORNING, ...COMMON_AFTERNOON_AND_EVENING]);
const HUNNAN_SCHEDULE = makeSchedule([...HUNNAN_MORNING, ...COMMON_AFTERNOON_AND_EVENING]);

export const TIMETABLE_CAMPUS_SCHEDULES = Object.freeze({
  nanhu: Object.freeze({ name: '南湖校区', short_name: '南', sections: EARLY_SCHEDULE }),
  east: Object.freeze({ name: '东校区', short_name: '东', sections: EARLY_SCHEDULE }),
  network: Object.freeze({ name: '网络区', short_name: '网', sections: EARLY_SCHEDULE }),
  hunnan: Object.freeze({ name: '浑南校区', short_name: '浑', sections: HUNNAN_SCHEDULE }),
});

const cleanCampusText = value => String(value || '').replace(/\s+/g, '').trim();

export const timetableCampusScheduleKey = (value, fallbackName = '') => {
  if (value && typeof value === 'object') {
    const nameCandidates = [value.campus_name, value.campusName, value.campus, value.name];
    for (const candidate of nameCandidates) {
      const named = timetableCampusScheduleKey(candidate);
      if (named) return named;
    }
    return timetableCampusScheduleKey(
      value.code || value.campus_code || value.campusCode,
      fallbackName,
    );
  }
  const text = cleanCampusText(value || fallbackName);
  if (!text) return '';
  if (text.includes('浑南')) return 'hunnan';
  if (text.includes('南湖')) return 'nanhu';
  if (text.includes('东校区') || text === '东区') return 'east';
  if (text.includes('网络区') || text.includes('网络校区')) return 'network';
  // These codes are confirmed for the current timetable/JWXK interfaces. A
  // supplied campus name is checked first so legacy fixtures with other code
  // systems cannot accidentally select the wrong schedule.
  if (text === '00') return 'nanhu';
  if (text === '01') return 'hunnan';
  return '';
};

const parseTime = value => {
  const match = String(value || '').match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
};

const formatTime = value => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
const SECTION_GAP_AFTER = Object.freeze({ 1: 10, 2: 20, 3: 10, 5: 10, 6: 20, 7: 10, 9: 10, 10: 10, 11: 10 });

const sameDayPart = (startSection, endSection) => (
  (startSection >= 1 && endSection <= 4)
  || (startSection >= 5 && endSection <= 8)
  || (startSection >= 9 && endSection <= 12)
);

const deriveCourseSections = course => {
  const startSection = Number(course?.start_section);
  const endSection = Number(course?.end_section || course?.start_section);
  const startMinutes = parseTime(course?.start_time);
  const endMinutes = parseTime(course?.end_time);
  if (
    !Number.isInteger(startSection)
    || !Number.isInteger(endSection)
    || startSection < 1
    || endSection > 12
    || endSection < startSection
    || startMinutes == null
    || !sameDayPart(startSection, endSection)
  ) return [];

  const derived = [];
  let cursor = startMinutes;
  for (let section = startSection; section <= endSection; section += 1) {
    derived.push({ number: section, start_time: formatTime(cursor), end_time: formatTime(cursor + 45) });
    cursor += 45;
    if (section < endSection) cursor += SECTION_GAP_AFTER[section] ?? 10;
  }
  if (endMinutes != null && parseTime(derived[derived.length - 1].end_time) !== endMinutes) return [];
  return derived;
};

const flattenCourses = courses => {
  if (Array.isArray(courses)) return courses;
  if (!courses || typeof courses !== 'object') return [];
  return Object.values(courses).flatMap(value => (Array.isArray(value) ? value : []));
};

const selectedCampusKeys = ({ campusCode, campusName, campuses, courses }) => {
  const selectedKey = timetableCampusScheduleKey(campusName || campusCode);
  const allCampuses = !campusCode || campusCode === 'all' || campusCode === '__all__';
  if (!allCampuses && selectedKey) return [selectedKey];

  const courseKeys = flattenCourses(courses)
    .map(course => timetableCampusScheduleKey(course))
    .filter(Boolean);
  // In all-campus mode the rendered courses are the narrowest reliable
  // description of the clocks that actually need to be shown.  Using the
  // complete campus catalog first adds unused Nanhu/Hunnan variants and makes
  // the section axis needlessly crowded for a one-campus result.
  if (courseKeys.length) return [...new Set(courseKeys)];
  const catalogKeys = (campuses || [])
    .filter(campus => !['all', '__all__'].includes(String(campus?.code || '')))
    .map(campus => timetableCampusScheduleKey(campus))
    .filter(Boolean);
  return [...new Set(catalogKeys)];
};

const officialSectionsByCampusKey = (sectionsByCampus, campuses) => {
  const campusByCode = new Map((campuses || []).map(campus => [String(campus?.code || ''), campus]));
  const result = new Map();
  Object.entries(sectionsByCampus || {}).forEach(([campusCode, rows]) => {
    if (['all', '__all__'].includes(String(campusCode)) || !Array.isArray(rows)) return;
    const key = timetableCampusScheduleKey(campusByCode.get(String(campusCode)) || campusCode);
    if (!key) return;
    if (!result.has(key)) result.set(key, new Map());
    const byNumber = result.get(key);
    rows.forEach(section => {
      const number = Number(section?.number);
      if (!Number.isFinite(number)) return;
      const previous = byNumber.get(number) || {};
      byNumber.set(number, {
        ...previous,
        ...section,
        start_time: String(section?.start_time || previous.start_time || '').trim(),
        end_time: String(section?.end_time || previous.end_time || '').trim(),
      });
    });
  });
  return result;
};

const courseOverridesByCampus = courses => {
  const candidates = new Map();
  flattenCourses(courses).forEach(course => {
    const key = timetableCampusScheduleKey(course);
    if (!key) return;
    deriveCourseSections(course).forEach(section => {
      const candidateKey = `${key}:${section.number}`;
      if (!candidates.has(candidateKey)) candidates.set(candidateKey, new Map());
      const values = candidates.get(candidateKey);
      const valueKey = `${section.start_time}|${section.end_time}`;
      values.set(valueKey, (values.get(valueKey) || 0) + 1);
    });
  });
  const overrides = new Map();
  candidates.forEach((values, key) => {
    if (values.size !== 1) return;
    const [value] = values.keys();
    const [startTime, endTime] = value.split('|');
    overrides.set(key, { start_time: startTime, end_time: endTime });
  });
  return overrides;
};

/**
 * Resolve display-only section times without mutating an official/cache payload.
 * Returned time_variants contains more than one row only when an all-campus
 * timetable contains campuses whose clocks differ for that section.
 */
export const resolveTimetableSections = ({
  sections = [],
  sectionsByCampus = {},
  courses = [],
  campusCode = '',
  campusName = '',
  campuses = [],
} = {}) => {
  const officialByNumber = new Map((sections || []).map(section => [Number(section.number), section]));
  const keys = selectedCampusKeys({ campusCode, campusName, campuses, courses });
  const allCampuses = !campusCode || campusCode === 'all' || campusCode === '__all__';
  const officialByCampusKey = officialSectionsByCampusKey(sectionsByCampus, campuses);
  const courseOverrides = courseOverridesByCampus(courses);
  const sectionNumbers = [...new Set([
    ...Array.from({ length: 12 }, (_, index) => index + 1),
    ...(sections || []).map(section => Number(section.number)).filter(Number.isFinite),
  ])].sort((left, right) => left - right);

  return sectionNumbers.map(number => {
    const official = officialByNumber.get(number) || {};
    const officialStart = String(official.start_time || '').trim();
    const officialEnd = String(official.end_time || '').trim();
    const variants = keys.map(key => {
      const profile = TIMETABLE_CAMPUS_SCHEDULES[key];
      const builtin = profile?.sections.find(section => section.number === number) || {};
      const courseOverride = courseOverrides.get(`${key}:${number}`) || {};
      const campusOfficial = officialByCampusKey.get(key)?.get(number) || {};
      const resolvedOfficial = allCampuses
        ? (Object.keys(campusOfficial).length ? campusOfficial : (keys.length === 1 ? official : {}))
        : official;
      const resolvedOfficialStart = String(resolvedOfficial.start_time || '').trim();
      const resolvedOfficialEnd = String(resolvedOfficial.end_time || '').trim();
      return {
        key,
        label: profile?.name || '',
        short_label: profile?.short_name || '',
        start_time: resolvedOfficialStart || courseOverride.start_time || builtin.start_time || '',
        end_time: resolvedOfficialEnd || courseOverride.end_time || builtin.end_time || '',
        source: resolvedOfficialStart || resolvedOfficialEnd
          ? 'official'
          : (courseOverride.start_time || courseOverride.end_time ? 'course' : (builtin.start_time ? 'builtin' : 'unknown')),
      };
    });

    const collapsed = [];
    variants.forEach(variant => {
      const existing = collapsed.find(item => (
        item.start_time === variant.start_time && item.end_time === variant.end_time
      ));
      if (existing) {
        existing.keys.push(variant.key);
        existing.labels.push(variant.label);
        existing.short_labels.push(variant.short_label);
        if (existing.source !== variant.source) existing.source = 'mixed';
      } else {
        collapsed.push({
          ...variant,
          keys: [variant.key],
          labels: [variant.label],
          short_labels: [variant.short_label],
        });
      }
    });

    if (!collapsed.length && (officialStart || officialEnd)) {
      collapsed.push({
        key: 'official', keys: ['official'], label: '', labels: [], short_label: '', short_labels: [],
        start_time: officialStart, end_time: officialEnd, source: 'official',
      });
    }

    const primary = collapsed.length === 1 ? collapsed[0] : null;
    const campusSectionName = [...officialByCampusKey.values()]
      .map(items => items.get(number)?.name)
      .find(Boolean);
    return {
      ...official,
      number,
      name: official.name || campusSectionName || `第${number}节`,
      start_time: primary?.start_time || (!collapsed.length ? officialStart : ''),
      end_time: primary?.end_time || (!collapsed.length ? officialEnd : ''),
      time_source: primary?.source || (collapsed.length ? 'multiple' : 'unknown'),
      time_variants: collapsed,
    };
  });
};
