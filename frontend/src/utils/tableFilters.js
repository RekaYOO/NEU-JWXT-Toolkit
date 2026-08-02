import { compareAcademicTermsNewestFirst } from './termSort';

export const compareTextValues = (a, b) => (
  String(a ?? '').localeCompare(String(b ?? ''), 'zh-CN', {
    numeric: true,
    sensitivity: 'base',
  })
);

export const uniqueFilterOptions = (values, compare = compareTextValues) => (
  [...new Set(values.filter(value => value !== null && value !== undefined && value !== ''))]
    .sort(compare)
    .map(value => ({ text: String(value), value }))
);

export const academicTermFilterOptions = (values) => (
  uniqueFilterOptions(values, compareAcademicTermsNewestFirst)
);
