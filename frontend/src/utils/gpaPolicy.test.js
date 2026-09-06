import { calculateGpaImpacts, gpaExclusionReason, summarizeGpa } from './gpaPolicy';

const courses = [
  { code: 'CORE', credit: 3, gpa: 4 },
  { code: 'FAIL', credit: 2, gpa: 0 },
  { code: 'GE', credit: 2, gpa: 5 },
  { code: 'BINARY', credit: 1, gpa: 5, gradingScale: '两级制' },
];
const modern = { mode: 'from_2025', general_elective_codes: ['GE'] };

test('modern policy removes both numerator and denominator, retaining zero GPA', () => {
  expect(summarizeGpa(courses, modern)).toEqual({ average: 2.4, credits: 5, points: 12, count: 2 });
  expect(summarizeGpa(courses, { mode: 'through_2024' }).average).toBe(27 / 8);
  expect(courses).toHaveLength(4);
});

test('descendant plan snapshots and cached scales follow the same exclusions', () => {
  expect(gpaExclusionReason({ categoryPath: '通识类 > 通识选修类 > 科学素养类', gpa: 4 }, modern)).toContain('通识选修');
  expect(gpaExclusionReason({ code: 'X' }, { ...modern, grading_scales: { X: '两级制' } })).toContain('两级制');
  expect(gpaExclusionReason({ courseCategory: '通识必修类', courseType: '选修' }, modern)).toBe('');
});

test('blank, invalid and zero-credit entries do not enter the denominator', () => {
  const invalid = [null, undefined, '', 'bad', Infinity, -1].map(gpa => ({ gpa, credit: 2 }));
  expect(summarizeGpa([...invalid, { gpa: 4, credit: 0 }], modern).average).toBeNull();
  expect(summarizeGpa([{ gpa: 0, credit: 1 }], modern).average).toBe(0);
  expect(summarizeGpa([{ gpa: 5, credit: 1, grading_scale: '两级制' }], modern).average).toBeNull();
});

test('GPA contributions use the same eligible course set', () => {
  const result = calculateGpaImpacts(courses, modern);
  expect(result[2].mean_adjust_delta).toBe(0);
  expect(result[3].exclude_delta).toBe(0);
  expect(result[0].mean_adjust_delta).toBeCloseTo(0.96);
  expect(result[0].exclude_delta).toBeCloseTo(2.4);
});
