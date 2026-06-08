const getAcademicTermParts = (value) => {
  const text = String(value || '').trim();
  if (!text) return null;

  const yearMatch = text.match(/(\d{4})\s*-\s*(\d{4})/);
  if (!yearMatch) return null;

  const codeMatch = text.match(/(\d{4})\s*-\s*(\d{4})\s*-\s*([12])/);
  const termCode = codeMatch?.[3];
  let semester = null;

  if (termCode === '2' || text.includes('春')) {
    semester = 'spring';
  } else if (termCode === '1' || text.includes('秋')) {
    semester = 'autumn';
  }

  return {
    startYear: Number(yearMatch[1]),
    endYear: Number(yearMatch[2]),
    semesterRank: semester === 'spring' ? 0 : semester === 'autumn' ? 1 : 2,
  };
};

export const compareAcademicTerms = (a, b) => {
  const aParts = getAcademicTermParts(a);
  const bParts = getAcademicTermParts(b);

  if (aParts && bParts) {
    if (aParts.startYear !== bParts.startYear) {
      return bParts.startYear - aParts.startYear;
    }
    if (aParts.endYear !== bParts.endYear) {
      return bParts.endYear - aParts.endYear;
    }
    if (aParts.semesterRank !== bParts.semesterRank) {
      return aParts.semesterRank - bParts.semesterRank;
    }
  }

  if (aParts && !bParts) return -1;
  if (!aParts && bParts) return 1;

  return String(a || '').localeCompare(String(b || ''), 'zh-CN');
};
