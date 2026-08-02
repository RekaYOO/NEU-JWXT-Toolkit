const parseAcademicTerm = (value) => {
  const text = String(value ?? '').trim();
  if (!text) return null;

  const yearMatch = text.match(/(\d{4})\s*-\s*(\d{4})/);
  if (!yearMatch) return null;

  const code = text.match(/\d{4}\s*-\s*\d{4}\s*-\s*([12])(?:\D|$)/)?.[1];
  let semester = null;
  if (code === '1' || text.includes('秋')) semester = 'autumn';
  if (code === '2' || text.includes('春')) semester = 'spring';
  if (!semester) return null;

  return {
    startYear: Number(yearMatch[1]),
    endYear: Number(yearMatch[2]),
    semesterRank: semester === 'autumn' ? 0 : semester === 'spring' ? 1 : 2,
  };
};

// Natural chronological order: autumn first, then spring in the same academic year.
export const compareAcademicTermsOldestFirst = (a, b) => {
  const left = parseAcademicTerm(a);
  const right = parseAcademicTerm(b);
  if (left && right) {
    return left.startYear - right.startYear
      || left.endYear - right.endYear
      || left.semesterRank - right.semesterRank;
  }
  // Table components reverse this comparator for descending order. Treat
  // unrecognized values as older so the default newest-first view keeps them last.
  if (left) return 1;
  if (right) return -1;
  return String(a ?? '').localeCompare(String(b ?? ''), 'zh-CN');
};

// Display-list order. Unknown and empty values stay behind recognized terms.
export const compareAcademicTermsNewestFirst = (a, b) => {
  const left = parseAcademicTerm(a);
  const right = parseAcademicTerm(b);
  if (left && right) return -compareAcademicTermsOldestFirst(a, b);
  if (left) return -1;
  if (right) return 1;
  return String(a ?? '').localeCompare(String(b ?? ''), 'zh-CN');
};

// Compatibility alias for existing newest-first display lists.
export const compareAcademicTerms = compareAcademicTermsNewestFirst;

export const sortAcademicTermsNewestFirst = (values) => (
  [...values].sort(compareAcademicTermsNewestFirst)
);
