import dayjs from 'dayjs';

export const activityStart = (activity) => (
  activity?.start_time
  || activity?.activity_start_time
  || activity?.activity_time_start
  || activity?.start_date
  || activity?.date
  || ''
);

export const semesterForDate = (value) => {
  const date = dayjs(value);
  if (!date.isValid()) return null;
  const year = date.year();
  const month = date.month() + 1;
  if (month >= 9) {
    return {
      key: `${year}-autumn`,
      label: `${year}-${year + 1} 秋季学期`,
      start: dayjs(`${year}-09-01`),
      end: dayjs(`${year + 1}-02-01`).endOf('month'),
    };
  }
  if (month <= 2) {
    return {
      key: `${year - 1}-autumn`,
      label: `${year - 1}-${year} 秋季学期`,
      start: dayjs(`${year - 1}-09-01`),
      end: dayjs(`${year}-02-01`).endOf('month'),
    };
  }
  return {
    key: `${year - 1}-spring`,
    label: `${year - 1}-${year} 春季学期`,
    start: dayjs(`${year}-03-01`),
    end: dayjs(`${year}-08-31`),
  };
};

export const currentSemester = (now = dayjs()) => semesterForDate(now);

export const semesterOptions = (activities = [], now = dayjs()) => {
  const semesters = new Map();
  const current = currentSemester(now);
  semesters.set(current.key, current);
  activities.forEach((activity) => {
    const semester = semesterForDate(activityStart(activity));
    if (semester) semesters.set(semester.key, semester);
  });
  return [...semesters.values()].sort((left, right) => (
    right.start.valueOf() - left.start.valueOf()
  ));
};

export const chooseInitialSemester = (activities = [], now = dayjs()) => {
  const current = currentSemester(now);
  const currentHasActivity = activities.some((activity) => {
    const value = dayjs(activityStart(activity));
    return value.isValid()
      && !value.isBefore(current.start, 'day')
      && !value.isAfter(current.end, 'day');
  });
  if (currentHasActivity || activities.length === 0) return current;
  return semesterOptions(activities, now).find((semester) => (
    semester.key !== current.key
    && activities.some((activity) => semesterForDate(activityStart(activity))?.key === semester.key)
  )) || current;
};

export const activityInRange = (activity, range) => {
  const value = dayjs(activityStart(activity));
  if (!value.isValid() || !range?.[0] || !range?.[1]) return false;
  return !value.isBefore(range[0], 'day') && !value.isAfter(range[1], 'day');
};

export const activityHasCertificate = (activity) => Boolean(
  activity?.certificate_available,
);

export const activityHasAward = (activity) => String(
  activity?.award || '',
).includes('奖');

export const academicYearForDate = (value) => {
  const date = dayjs(value);
  if (!date.isValid()) return null;
  const year = date.year();
  const boundary = dayjs(`${year}-08-31`);
  const startYear = date.isBefore(boundary, 'day') ? year - 1 : year;
  return {
    key: `${startYear}-academic-year`,
    label: `${startYear}-${startYear + 1} 学年`,
    start: dayjs(`${startYear}-08-31`),
    end: dayjs(`${startYear + 1}-08-30`),
  };
};

export const currentAcademicYear = (now = dayjs()) => academicYearForDate(now);

export const academicYearOptions = (activities = [], now = dayjs()) => {
  const years = new Map();
  const current = currentAcademicYear(now);
  years.set(current.key, current);
  activities.forEach((activity) => {
    const academicYear = academicYearForDate(activityStart(activity));
    if (academicYear) years.set(academicYear.key, academicYear);
  });
  return [...years.values()].sort((left, right) => (
    right.start.valueOf() - left.start.valueOf()
  ));
};

export const academicYearChoices = (activities = [], now = dayjs()) => {
  const current = currentAcademicYear(now);
  const years = new Map(
    academicYearOptions(activities, now).map(item => [item.key, item]),
  );
  const startYear = current.start.year();
  for (let offset = -4; offset <= 1; offset += 1) {
    const item = academicYearForDate(`${startYear + offset}-08-31`);
    years.set(item.key, item);
  }
  return [...years.values()].sort((left, right) => (
    right.start.valueOf() - left.start.valueOf()
  ));
};
