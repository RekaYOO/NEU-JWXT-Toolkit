import {
  compareAcademicTermsNewestFirst,
  compareAcademicTermsOldestFirst,
  sortAcademicTermsNewestFirst,
} from './termSort';
import { academicTermFilterOptions } from './tableFilters';

test('academic terms follow autumn then spring chronology and display newest first', () => {
  expect(sortAcademicTermsNewestFirst([
    '',
    'unknown',
    '2025-2026',
    '2024-2025-2',
    '2025-2026-1',
    '2024-2025-1',
    '2025-2026-2',
  ])).toEqual([
    '2025-2026-2',
    '2025-2026-1',
    '2024-2025-2',
    '2024-2025-1',
    '',
    '2025-2026',
    'unknown',
  ]);

  expect(compareAcademicTermsOldestFirst('2024-2025-1', '2024-2025-2')).toBeLessThan(0);
  expect(compareAcademicTermsNewestFirst('2024-2025-1', '2024-2025-2')).toBeGreaterThan(0);
  expect(compareAcademicTermsNewestFirst('2025-2026-1', '2024-2025-2')).toBeLessThan(0);
});

test('term filters are de-duplicated and ordered newest first', () => {
  expect(academicTermFilterOptions([
    '2024-2025-1',
    '2025-2026-2',
    '2024-2025-2',
    '2025-2026-2',
    null,
  ])).toEqual([
    { text: '2025-2026-2', value: '2025-2026-2' },
    { text: '2024-2025-2', value: '2024-2025-2' },
    { text: '2024-2025-1', value: '2024-2025-1' },
  ]);
});
