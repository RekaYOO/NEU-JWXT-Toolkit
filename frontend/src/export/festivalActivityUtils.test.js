import dayjs from 'dayjs';
import {
  academicYearChoices, academicYearForDate, academicYearOptions, activityHasAward, activityInRange,
  chooseInitialSemester, currentAcademicYear,
  semesterForDate, semesterOptions,
} from './festivalActivityUtils';

describe('festival activity semester rules', () => {
  test('maps a spring date to the academic year that began the previous autumn', () => {
    const semester = semesterForDate('2026-03-18');
    expect(semester.key).toBe('2025-spring');
    expect(semester.label).toBe('2025-2026 春季学期');
    expect(semester.start.format('YYYY-MM-DD')).toBe('2026-03-01');
    expect(semester.end.format('YYYY-MM-DD')).toBe('2026-08-31');
  });

  test('maps January and February to the preceding autumn semester', () => {
    const semester = semesterForDate('2026-02-28');
    expect(semester.key).toBe('2025-autumn');
    expect(semester.start.format('YYYY-MM-DD')).toBe('2025-09-01');
    expect(semester.end.format('YYYY-MM-DD')).toBe('2026-02-28');
  });

  test('uses inclusive date boundaries and excludes unknown times', () => {
    const range = [dayjs('2025-09-01'), dayjs('2026-02-28')];
    expect(activityInRange({ start_time: '2025-09-01T08:00:00+08:00' }, range)).toBe(true);
    expect(activityInRange({ start_time: '2026-02-28' }, range)).toBe(true);
    expect(activityInRange({ start_time: null }, range)).toBe(false);
  });

  test('falls back to the most recent semester containing an activity', () => {
    const activities = [
      { start_time: '2025-10-10' },
      { start_time: '2026-04-20' },
    ];
    const selected = chooseInitialSemester(activities, dayjs('2026-10-01'));
    expect(selected.key).toBe('2025-spring');
    expect(semesterOptions(activities, dayjs('2026-10-01')).map(item => item.key))
      .toEqual(['2026-autumn', '2025-spring', '2025-autumn']);
  });
});

describe('festival activity academic year selection', () => {
  test('starts the current academic year on August 31', () => {
    expect(currentAcademicYear(dayjs('2026-08-31')).key).toBe('2026-academic-year');
    expect(currentAcademicYear(dayjs('2026-08-31')).start.format('YYYY-MM-DD'))
      .toBe('2026-08-31');
    expect(currentAcademicYear(dayjs('2026-08-31')).end.format('YYYY-MM-DD'))
      .toBe('2027-08-30');
  });

  test('uses the previous start year before August 31', () => {
    const academicYear = academicYearForDate('2026-08-30');
    expect(academicYear.key).toBe('2025-academic-year');
    expect(academicYear.label).toBe('2025-2026 学年');
    expect(academicYear.start.format('YYYY-MM-DD')).toBe('2025-08-31');
    expect(academicYear.end.format('YYYY-MM-DD')).toBe('2026-08-30');
  });

  test('offers only the current year and years discovered from real activities', () => {
    expect(academicYearOptions([], dayjs('2026-08-02')).map(item => item.key))
      .toEqual(['2025-academic-year']);
    expect(academicYearOptions(
      [{ start_time: '2023-10-01' }],
      dayjs('2026-08-02'),
    ).map(item => item.key)).toEqual(['2025-academic-year', '2023-academic-year']);
  });

  test('keeps the current academic year selected while 2021 remains only a shortcut option', () => {
    const now = dayjs('2026-08-02');
    expect(currentAcademicYear(now).key).toBe('2025-academic-year');
    expect(academicYearChoices([], now).map(item => item.key)).toEqual([
      '2026-academic-year',
      '2025-academic-year',
      '2024-academic-year',
      '2023-academic-year',
      '2022-academic-year',
      '2021-academic-year',
    ]);
  });

  test('treats only award text containing the Chinese award marker as awarded', () => {
    expect(activityHasAward({ award: '一等奖' })).toBe(true);
    expect(activityHasAward({ award: '优秀作品' })).toBe(false);
    expect(activityHasAward({ award: '' })).toBe(false);
  });
});
