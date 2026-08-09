import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Grid,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Skeleton,
  Space,
  Tabs,
  Tag,
  Tooltip,
} from 'antd';
import {
  EnvironmentOutlined,
  FilterOutlined,
  ReloadOutlined,
} from '@ant-design/icons';

import {
  MobileDetailDrawer,
  MobileFilterDrawer,
} from '../components/mobile/MobileUX';
import {
  getTimetableContext,
  getPersonalTimetable,
  getTimetableSchedule,
  getTimetableTargetFilterOptions,
  getTimetableTerms,
  searchTimetableTargets,
} from '../services/api';
import { useResourceMemory } from '../resources/ResourceStore';
import { compareAcademicTermsNewestFirst } from '../utils/termSort';
import './TimetablePage.css';

const { useBreakpoint } = Grid;

export const TIMETABLE_MODES = [
  { key: 'personal', label: '我的课表' },
  { key: 'class', label: '班级课表' },
  { key: 'teacher', label: '教师课表' },
  { key: 'room', label: '教室课表' },
];

const MODE_LABELS = Object.fromEntries(TIMETABLE_MODES.map(item => [item.key, item.label]));
const WEEKDAY_NAMES = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'];
const SHORT_WEEKDAY_NAMES = ['一', '二', '三', '四', '五', '六', '日'];
export const TIMETABLE_DAY_ORDER = [7, 1, 2, 3, 4, 5, 6];
const DETAIL_LABELS = {
  campus: '校区',
  building: '教学楼',
  floor: '楼层',
  type: '类型',
  department: '单位',
  use_scope: '使用范围',
  lab_center: '实验中心',
  capacity: '容量',
  title: '职称',
  gender: '性别',
  external: '外聘',
  grade: '年级',
  college: '学院',
  major: '专业',
  direction: '专业方向',
};

const TARGET_FILTER_DEFINITIONS = {
  class: [
    ['grade', '年级'], ['college', '学院'], ['major', '专业'],
    ['direction', '专业方向'], ['campus', '校区'],
  ],
  teacher: [
    ['department', '所在单位'], ['title', '职称'], ['gender', '性别'], ['external', '是否外聘'],
  ],
  room: [
    ['campus', '校区'], ['building', '教学楼'], ['floor', '楼层'], ['room_type', '教室类型'],
    ['department', '管理单位'], ['use_scope', '使用范围'], ['lab_center', '实验中心'],
  ],
};

const TARGET_FILTER_DESCENDANTS = {
  class: {
    grade: ['college', 'major', 'direction'],
    college: ['major', 'direction'],
    major: ['direction'],
  },
  teacher: {
    department: ['title'],
  },
  room: {
    campus: ['building', 'floor', 'room_type', 'department', 'use_scope', 'lab_center'],
    building: ['floor', 'room_type', 'use_scope', 'lab_center'],
  },
};

const TARGET_FILTER_REQUIRED_PARENTS = {
  class: { major: 'college', direction: 'major' },
  room: { building: 'campus', floor: 'building' },
};

const emptyModeSession = () => ({
  target: null,
  options: [],
  filterOptions: {},
  filterRelations: [],
  filterOptionsLoadedFor: '',
  filters: {},
  search: { keyword: '', page: 0, total: 0 },
});
const createModeSessions = () => ({
  class: emptyModeSession(),
  teacher: emptyModeSession(),
  room: emptyModeSession(),
});

export const preserveModeSessionsForTermChange = sessions => Object.fromEntries(
  Object.entries(sessions).map(([key, saved]) => [key, {
    ...saved,
    target: null,
    options: [],
    filterOptions: {},
    filterRelations: [],
    filterOptionsLoadedFor: '',
    search: { keyword: saved.search?.keyword || '', page: 0, total: 0 },
  }]),
);

const todayWeekday = (now = new Date()) => {
  const day = now.getDay();
  return day === 0 ? 7 : day;
};

const normalizeDate = value => {
  if (!value) return null;
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
};

const dateKey = value => {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const expectedAcademicTerm = now => {
  const month = now.getMonth() + 1;
  const startYear = month >= 8 ? now.getFullYear() : now.getFullYear() - 1;
  const semester = month >= 8 || month === 1 ? 1 : 2;
  return { startYear, endYear: startYear + 1, semester };
};

const termMatchesDate = (term, now) => {
  const { startYear, endYear, semester } = expectedAcademicTerm(now);
  const text = `${term.code || ''} ${term.name || ''}`;
  if (!text.includes(String(startYear)) || !text.includes(String(endYear))) return false;
  if (semester === 1) return /(?:-|\s|第)1(?:学期|\D|$)|秋/.test(text);
  return /(?:-|\s|第)2(?:学期|\D|$)|春/.test(text);
};

export const selectEffectiveCurrentTerm = (terms = [], currentCode = '', now = new Date()) => {
  if (!terms.length) return '';
  const authoritative = terms.find(item => item.code === currentCode);
  if (authoritative) return authoritative.code;
  const flagged = terms.find(item => item.current);
  if (flagged) return flagged.code;
  const inferred = terms.find(item => termMatchesDate(item, now));
  if (inferred) return inferred.code;
  return '';
};

export const selectDefaultTerm = (terms = [], currentCode = '', now = new Date()) => {
  if (!terms.length) return '';
  const effectiveCurrent = selectEffectiveCurrentTerm(terms, currentCode, now);
  if (effectiveCurrent) return effectiveCurrent;
  return [...terms]
    .sort((left, right) => compareAcademicTermsNewestFirst(
      `${left.code || ''} ${left.name || ''}`,
      `${right.code || ''} ${right.name || ''}`,
    ))[0]?.code || '';
};

export const selectDefaultWeek = (weeks = [], { currentTerm = true, now = new Date() } = {}) => {
  if (!weeks.length) return null;
  if (!currentTerm) return weeks[0].number;
  const today = dateKey(now);
  const containing = weeks.find(item => (
    item.start_date && item.end_date
    && today >= String(item.start_date).slice(0, 10)
    && today <= String(item.end_date).slice(0, 10)
  ));
  if (containing) return containing.number;
  const flagged = weeks.find(item => item.current);
  if (flagged) return flagged.number;
  const dated = weeks
    .map(item => ({ item, start: normalizeDate(item.start_date), end: normalizeDate(item.end_date) }))
    .filter(item => item.start || item.end)
    .sort((left, right) => {
      const leftDistance = Math.min(
        Math.abs((left.start || left.end).getTime() - now.getTime()),
        Math.abs((left.end || left.start).getTime() - now.getTime()),
      );
      const rightDistance = Math.min(
        Math.abs((right.start || right.end).getTime() - now.getTime()),
        Math.abs((right.end || right.start).getTime() - now.getTime()),
      );
      return leftDistance - rightDistance;
    });
  return dated[0]?.item.number || weeks[0].number;
};

export const immediateNextTerm = (terms = [], currentCode = '') => {
  const chronological = [...terms].sort((left, right) => -compareAcademicTermsNewestFirst(
    `${left.code || ''} ${left.name || ''}`,
    `${right.code || ''} ${right.name || ''}`,
  ));
  const index = chronological.findIndex(item => item.code === currentCode);
  return index >= 0 ? chronological[index + 1]?.code || '' : '';
};

const sectionRangesOverlap = (left, right) => (
  left.start_section <= right.end_section && right.start_section <= left.end_section
);

const courseIntersects = (left, right) => {
  const sectionsOverlap = sectionRangesOverlap(left, right);
  if (!sectionsOverlap) return false;
  const leftWeeks = Array.isArray(left.weeks) ? left.weeks : [];
  const rightWeeks = Array.isArray(right.weeks) ? right.weeks : [];
  if (
    !left.recurrence_unknown
    && !right.recurrence_unknown
    && leftWeeks.length
    && rightWeeks.length
  ) {
    return leftWeeks.some(week => rightWeeks.includes(week));
  }
  return true;
};

export const layoutDayCourses = (courses = []) => {
  const sorted = [...courses].sort((a, b) => (
    a.start_section - b.start_section || a.end_section - b.end_section
  ));
  const groups = [];
  sorted.forEach(course => {
    const group = groups.find(item => item.some(existing => sectionRangesOverlap(existing, course)));
    if (group) group.push(course);
    else groups.push([course]);
  });
  return groups.flatMap(group => {
    const laneEnds = [];
    const placed = group.map(course => {
      let lane = laneEnds.findIndex(end => end < course.start_section);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = course.end_section;
      return { course, lane };
    });
    const laneCount = Math.max(laneEnds.length, 1);
    return placed.map(({ course, lane }) => ({
      ...course,
      lane,
      laneCount,
      hasActualConflict: group.some(other => other !== course && courseIntersects(course, other)),
    }));
  });
};

export const groupDayCourses = (courses = []) => {
  const sorted = [...courses].sort((a, b) => (
    a.start_section - b.start_section || b.end_section - a.end_section
  ));
  const groups = [];
  sorted.forEach(course => {
    const group = groups.find(item => item.courses.some(existing => sectionRangesOverlap(existing, course)));
    if (group) {
      group.courses.push(course);
      group.start_section = Math.min(group.start_section, course.start_section);
      group.end_section = Math.max(group.end_section, course.end_section);
    } else {
      groups.push({
        start_section: course.start_section,
        end_section: course.end_section,
        courses: [course],
      });
    }
  });
  return groups.map(group => {
    const orderedCourses = [...group.courses].sort((left, right) => (
      left.start_section - right.start_section
      || right.end_section - left.end_section
      || String(left.id || '').localeCompare(String(right.id || ''))
    ));
    return {
      ...group,
      courses: orderedCourses.map(course => ({
      ...course,
      hasActualConflict: group.courses.some(other => other !== course && courseIntersects(course, other)),
      })),
    };
  });
};

export const clusterLayoutMetrics = (height, courseCount, requestedFoldedHeights = []) => {
  const headerHeight = 3;
  const availableHeight = Math.max(height - headerHeight - 3, 64);
  const gap = -3;
  const minimumFoldedHeight = 40;
  const minimumExpandedHeight = 96;
  const capacity = Math.max(2, 1 + Math.floor(
    Math.max(availableHeight - minimumExpandedHeight + gap, 0) / (minimumFoldedHeight + gap),
  ));
  const hasHiddenCourses = courseCount > capacity;
  const visibleCourseCount = hasHiddenCourses ? Math.max(1, capacity - 1) : courseCount;
  const foldedHeights = Array.from({ length: visibleCourseCount }, (_, index) => (
    Math.max(30, Number(requestedFoldedHeights[index] || minimumFoldedHeight))
  ));
  const rowCount = Math.max(visibleCourseCount + (hasHiddenCourses ? 1 : 0), 1);
  const foldedCount = Math.max(rowCount - 1, 0);
  const largestFoldedHeight = foldedCount
    ? Math.floor((availableHeight - minimumExpandedHeight - gap * foldedCount) / foldedCount)
    : minimumFoldedHeight;
  const foldedHeight = Math.max(30, Math.min(minimumFoldedHeight, largestFoldedHeight));
  const expandedHeight = Math.max(
    38,
    availableHeight - foldedHeight * foldedCount - gap * Math.max(rowCount - 1, 0),
  );
  return {
    capacity,
    hasHiddenCourses,
    visibleCourseCount,
    expandedHeight,
    foldedHeight,
    foldedHeights,
    gap,
    headerHeight,
    availableHeight,
  };
};

export const clusterDisplayCapacity = height => clusterLayoutMetrics(height, Number.MAX_SAFE_INTEGER).capacity;

export const clusterStackLayout = (metrics, visibleCourseCount, activeIndex, hasMore = false) => {
  const contentBottom = metrics.headerHeight + metrics.availableHeight;
  const courses = Array.from({ length: visibleCourseCount });
  let cursor = metrics.headerHeight;
  for (let courseIndex = 0; courseIndex < activeIndex; courseIndex += 1) {
    const foldedHeight = metrics.foldedHeights?.[courseIndex] || metrics.foldedHeight;
    courses[courseIndex] = {
      courseIndex,
      expanded: false,
      top: cursor,
      height: foldedHeight,
    };
    cursor += foldedHeight + metrics.gap;
  }
  const activeTop = cursor;
  courses[activeIndex] = {
    courseIndex: activeIndex,
    expanded: true,
    top: activeTop,
    height: Math.max(metrics.expandedHeight, contentBottom - activeTop),
  };
  let bottomCursor = contentBottom;
  let more = null;
  if (hasMore) {
    more = { top: bottomCursor - metrics.foldedHeight, height: metrics.foldedHeight };
    bottomCursor = more.top + metrics.gap;
  }
  for (let courseIndex = visibleCourseCount - 1; courseIndex > activeIndex; courseIndex -= 1) {
    const foldedHeight = metrics.foldedHeights?.[courseIndex] || metrics.foldedHeight;
    const top = bottomCursor - foldedHeight;
    courses[courseIndex] = {
      courseIndex,
      expanded: false,
      top,
      height: foldedHeight,
    };
    bottomCursor = top + metrics.gap;
  }
  return { courses, more };
};

const estimatedTextLines = (value, width = 9) => {
  const units = Array.from(String(value || '')).reduce((total, character) => (
    total + (/^[\u0000-\u00ff]$/.test(character) ? 0.55 : 1)
  ), 0);
  return Math.max(1, Math.ceil(units / width));
};

export const estimatedFoldedCourseHeight = (course, viewMode = 'term') => {
  const content = courseCardContent(course);
  const titleLines = estimatedTextLines(content.name, 6.5);
  const summaryLines = viewMode === 'term' ? 1 : estimatedTextLines(`第${course.start_section || 1}–${course.end_section || course.start_section || 1}节`, 10);
  return Math.max(40, Math.ceil(8 + titleLines * 18 + summaryLines * 15));
};

export const estimatedCourseCardHeight = (course, viewMode = 'term', mode = 'personal') => {
  const content = courseCardContent(course);
  const contextText = courseContextText(course, mode);
  const titleLines = estimatedTextLines(content.name, 7);
  const locationLines = estimatedTextLines(content.location, 8);
  const contextLines = contextText ? estimatedTextLines(contextText, 8) : 0;
  const typeLines = content.type ? estimatedTextLines(content.type, 9) : 0;
  const informationLines = locationLines + contextLines + typeLines + (viewMode === 'term' ? 1 : 0) + 1;
  return Math.max(76, 14 + titleLines * 19 + informationLines * 16 + Math.max(informationLines, 1) * 2);
};

export const adaptiveSectionHeights = (sections, coursesByDay, viewMode = 'term', mode = 'personal') => {
  const heights = Array.from({ length: sections.length }, () => 64);
  const constraints = [];
  TIMETABLE_DAY_ORDER.forEach(day => {
    groupDayCourses(coursesByDay[day] || []).forEach(group => {
      const start = Math.max(Number(group.start_section || 1) - 1, 0);
      const end = Math.min(Math.max(Number(group.end_section || group.start_section || 1), start + 1), sections.length);
      const expandedRequired = Math.max(...group.courses.map(course => estimatedCourseCardHeight(course, viewMode, mode)));
      const foldedHeights = group.courses.map(course => estimatedFoldedCourseHeight(course, viewMode));
      const required = group.courses.length === 1
        ? expandedRequired
        : Math.max(...group.courses.map((course, activeIndex) => (
          estimatedCourseCardHeight(course, viewMode, mode)
          + foldedHeights.reduce((total, value, foldedIndex) => (
            total + (foldedIndex === activeIndex ? 0 : value)
          ), 0)
          - 3 * (group.courses.length - 1)
        )));
      constraints.push({ start, end, required: required + 6 });
    });
  });
  constraints
    .sort((left, right) => (left.end - left.start) - (right.end - right.start))
    .forEach(({ start, end, required }) => {
      if (end <= start) return;
      const current = heights.slice(start, end).reduce((total, value) => total + value, 0);
      const deficit = Math.max(required - current, 0);
      if (!deficit) return;
      const addition = deficit / (end - start);
      for (let index = start; index < end; index += 1) heights[index] += addition;
    });
  return heights.map(value => Math.ceil(value));
};

const uniqueTexts = values => [...new Set(values.filter(Boolean).map(value => String(value).trim()).filter(Boolean))];

export const formatWeekNumbers = (weeks = []) => {
  const values = [...new Set(weeks.filter(Number.isInteger))].sort((left, right) => left - right);
  if (!values.length) return '';
  const ranges = [];
  let start = values[0];
  let end = values[0];
  values.slice(1).forEach(value => {
    if (value === end + 1) {
      end = value;
      return;
    }
    ranges.push(start === end ? `${start}` : `${start}–${end}`);
    start = value;
    end = value;
  });
  ranges.push(start === end ? `${start}` : `${start}–${end}`);
  return `${ranges.join('、')} 周`;
};

export const courseCardContent = course => {
  const rawLocation = String(course.location || '').trim();
  const location = uniqueTexts([
    course.campus && !rawLocation.includes(course.campus) ? course.campus : '',
    rawLocation,
  ]).join(' · ') || '地点待定';
  const tagTexts = course.tags || [];
  const nature = course.course_nature
    || course.course_type
    || course.course_category
    || tagTexts.find(tag => /必修|选修/.test(tag))
    || '';
  const assessment = course.assessment_type
    || tagTexts.find(tag => /考试|考查/.test(tag))
    || '';
  const type = uniqueTexts([nature, assessment]).join(' · ')
    || course.activity_type_label
    || '';
  return {
    name: course.course_name || '未命名课程',
    location,
    type,
  };
};

const courseContextText = (course, mode) => {
  const teachers = uniqueTexts(course.teachers || []).join('、');
  const classes = uniqueTexts(course.classes || []).join('、');
  if (mode === 'teacher') return classes || teachers;
  if (mode === 'room') return uniqueTexts([classes, teachers]).join(' · ');
  return teachers || classes;
};

// Compatibility helper retained for callers/tests; card rendering no longer trusts official line order.
export const courseVisibleLines = course => {
  const content = courseCardContent(course);
  return [content.name, content.location, content.type].filter(Boolean);
};

const gradeRank = value => {
  const text = String(value || '');
  const fullYear = text.match(/(?:19|20)\d{2}/);
  if (fullYear) return Number(fullYear[0]);
  const shortYear = text.match(/(?:^|\D)(\d{2})(?:\D|$)/);
  return shortYear ? 2000 + Number(shortYear[1]) : Number.NEGATIVE_INFINITY;
};

export const sortGradeOptionsNewestFirst = options => [...(options || [])].sort((left, right) => (
  gradeRank(`${right.label || ''} ${right.value || ''}`)
  - gradeRank(`${left.label || ''} ${left.value || ''}`)
));

export const sortTargetsByRecentGrade = items => [...(items || [])].sort((left, right) => (
  gradeRank(`${right.details?.grade || ''} ${right.filter_values?.grade || ''}`)
  - gradeRank(`${left.details?.grade || ''} ${left.filter_values?.grade || ''}`)
));

export const mergeTargetOptions = (previous, next) => {
  const byId = new Map(previous.map(item => [item.id, item]));
  next.forEach(item => byId.set(item.id, item));
  return sortTargetsByRecentGrade([...byId.values()]);
};

export const mergeTargetFilterOptions = (catalog = [], loaded = []) => {
  const values = new Map();
  [...catalog, ...loaded].forEach(option => {
    if (option?.value && option?.label) values.set(option.value, option.label);
  });
  return [...values.entries()]
    .sort((left, right) => String(left[1]).localeCompare(String(right[1]), 'zh-CN'))
    .map(([value, label]) => ({ value, label }));
};

export const facetTargetFilterOptions = (
  key,
  catalog = [],
  relations = [],
  filters = {},
  filterOrder = [],
) => {
  const currentIndex = filterOrder.indexOf(key);
  const active = Object.entries(filters).filter(([filterKey, value]) => (
    filterKey !== key
    && value !== ''
    && value != null
    && !['has_schedule', 'min_capacity', 'max_capacity'].includes(filterKey)
    && (!filterOrder.length || (
      filterOrder.indexOf(filterKey) >= 0
      && filterOrder.indexOf(filterKey) < currentIndex
    ))
  ));
  if (!active.length || !relations.length) return catalog;
  const allowed = new Set(
    relations
      .filter(relation => active.every(([filterKey, value]) => relation[filterKey] === String(value)))
      .map(relation => relation[key])
      .filter(Boolean),
  );
  return catalog.filter(option => allowed.has(String(option.value)));
};

export const targetFilterMissingParent = (mode, key, filters = {}) => {
  const parent = TARGET_FILTER_REQUIRED_PARENTS[mode]?.[key];
  return parent && !filters[parent] ? parent : '';
};

export const updateTargetFilterDraft = (
  mode,
  previous,
  key,
  value,
  relations = [],
  filterOrder = [],
) => {
  const next = { ...previous };
  if (value === '' || value == null) delete next[key];
  else next[key] = value;
  const descendants = TARGET_FILTER_DESCENDANTS[mode]?.[key] || [];
  if (!relations.length || !filterOrder.length) {
    descendants.forEach(descendant => delete next[descendant]);
    return next;
  }
  descendants.forEach(descendant => {
    if (next[descendant] == null) return;
    if (targetFilterMissingParent(mode, descendant, next)) {
      delete next[descendant];
      return;
    }
    const descendantIndex = filterOrder.indexOf(descendant);
    const relevant = filterOrder.slice(0, descendantIndex + 1)
      .filter(filterKey => next[filterKey] !== '' && next[filterKey] != null);
    const remainsValid = relations.some(relation => relevant.every(filterKey => (
      relation[filterKey] === String(next[filterKey])
    )));
    if (!remainsValid) delete next[descendant];
  });
  return next;
};

export const shouldLoadMoreTargets = ({ loading, loaded, total, scrollTop, clientHeight, scrollHeight }) => (
  !loading && loaded < total && scrollTop + clientHeight >= scrollHeight - 24
);

export const capacityRangeInvalid = filters => (
  Number.isFinite(filters?.min_capacity)
  && Number.isFinite(filters?.max_capacity)
  && filters.min_capacity > filters.max_capacity
);

export const usableTargetFilterDefinitions = (
  definitions,
) => definitions || [];

export const preferredMobileDay = (coursesByDay, today = todayWeekday()) => {
  if ((coursesByDay[today] || []).length) return today;
  const teachingDays = TIMETABLE_DAY_ORDER.filter(day => (coursesByDay[day] || []).length);
  if (!teachingDays.length) return today;
  return teachingDays.sort((left, right) => Math.abs(left - today) - Math.abs(right - today))[0];
};

const timeToMinutes = value => {
  const match = String(value || '').match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
};

export const isCourseHappeningNow = (
  course,
  {
    now = new Date(),
    currentTerm = false,
    currentWeekNumber = null,
  } = {},
) => {
  if (!course || !currentTerm || !currentWeekNumber) return false;
  if (course.weekday !== todayWeekday(now)) return false;
  if (
    course.recurrence_unknown
    || !Array.isArray(course.weeks)
    || !course.weeks.includes(currentWeekNumber)
  ) return false;
  const start = timeToMinutes(course.start_time);
  const end = timeToMinutes(course.end_time);
  if (start == null || end == null) return false;
  const current = now.getHours() * 60 + now.getMinutes();
  return current >= start && current <= end;
};

export const shouldHighlightToday = ({
  termCode,
  currentTermCode,
  viewMode,
  weekNumber,
  currentWeekNumber,
}) => Boolean(
  termCode
  && termCode === currentTermCode
  && (viewMode === 'term' || weekNumber === currentWeekNumber),
);

export const courseMatchesWeek = (course, weekNumber) => {
  if (!weekNumber) return true;
  if (course.recurrence_unknown || !Array.isArray(course.weeks) || !course.weeks.length) return true;
  return course.weeks.includes(weekNumber);
};

const courseMatchesCampus = (course, campusCode, campuses) => {
  if (!campusCode || campuses.length <= 1 || campusCode === 'all' || campusCode === '__all__') return true;
  if (!course.campus_code && !course.campus) return true;
  const campus = campuses.find(item => item.code === campusCode);
  return course.campus_code === campusCode || course.campus === campus?.name;
};

export const personalScheduleView = (payload, campusCode, viewMode, weekNumber) => ({
  mode: 'personal',
  term_code: payload.term_code,
  campus_code: campusCode,
  target_id: '',
  week: viewMode === 'week' ? weekNumber : null,
  courses: (payload.courses || []).filter(course => (
    courseMatchesCampus(course, campusCode, payload.campuses || [])
    && (viewMode !== 'week' || courseMatchesWeek(course, weekNumber))
  )),
  unscheduled: payload.unscheduled || [],
  practices: payload.practices || [],
  source: payload.source,
  is_fresh: payload.is_fresh,
  last_update: payload.last_update,
  cache: payload.cache,
});

export const restorePersonalTimetableMemory = (memory, requestedTerm = '') => {
  const payload = memory?.payload;
  const currentTermCode = memory?.currentTermCode || payload?.term_code || '';
  if (
    !payload
    || !currentTermCode
    || payload.term_code !== currentTermCode
    || (requestedTerm && requestedTerm !== currentTermCode)
  ) return null;
  const campuses = payload.campuses || [];
  const campusCode = campuses.some(item => item.code === memory.campusCode)
    ? memory.campusCode
    : campuses[0]?.code || '';
  const viewMode = memory.viewMode === 'term' ? 'term' : 'week';
  const weeks = payload.weeks || [];
  const weekNumber = weeks.some(item => item.number === memory.weekNumber)
    ? memory.weekNumber
    : selectDefaultWeek(weeks, { currentTerm: true });
  const sectionsByCampus = payload.sections_by_campus || {};
  const sections = sectionsByCampus[campusCode]
    || Object.values(sectionsByCampus).find(rows => Array.isArray(rows) && rows.length)
    || [];
  return {
    terms: memory.terms || [],
    currentTermCode,
    termCode: currentTermCode,
    context: { campuses, weeks, sections },
    campusCode,
    viewMode,
    weekNumber,
    personalPayload: payload,
    schedule: personalScheduleView(payload, campusCode, viewMode, weekNumber),
  };
};

const targetDescription = target => Object.entries(target?.details || {})
  .map(([key, value]) => `${DETAIL_LABELS[key] || key}：${value}`)
  .join(' · ');

const targetOptionSummary = target => {
  const priorityKeys = target.details?.building
    ? ['campus', 'building', 'capacity', 'type']
    : target.details?.grade
      ? ['grade', 'college', 'major', 'direction']
      : ['department', 'title', 'external'];
  return priorityKeys
    .map(key => target.details?.[key])
    .filter(Boolean)
    .slice(0, 3)
    .join(' · ');
};

function TargetResultPanel({
  items,
  loading,
  total,
  mode,
  onSelect,
  onLoadMore,
  compact = false,
}) {
  const label = MODE_LABELS[mode]?.replace('课表', '') || '对象';
  return (
    <section className={`timetable-target-results${compact ? ' is-compact' : ''}`} aria-label={`${label}查询结果`}>
      <header>
        <div>
          <strong>{compact ? `选择${label}` : `符合条件的${label}`}</strong>
          <span>{total ? `共 ${total} 项，已加载 ${items.length} 项` : '可直接从结果中选择'}</span>
        </div>
        {loading && <span className="timetable-target-results-loading">正在加载…</span>}
      </header>
      {items.length ? (
        <div className="timetable-target-result-grid">
          {items.map(item => (
            <button
              type="button"
              key={item.id}
              className="timetable-target-result-card"
              onClick={() => onSelect(item)}
            >
              <strong>{item.name}</strong>
              <span>{item.id}</span>
              {targetOptionSummary(item) && <small>{targetOptionSummary(item)}</small>}
              <em>查看课表</em>
            </button>
          ))}
        </div>
      ) : !loading ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前搜索与筛选条件下没有匹配对象" />
      ) : null}
      {items.length < total && (
        <Button block loading={loading} onClick={onLoadMore}>加载更多结果</Button>
      )}
    </section>
  );
}

export const requestErrorText = (error, fallback, timeoutFallback = fallback) => {
  const detail = error.response?.data?.detail;
  if (detail) return detail;
  if (error.code === 'ECONNABORTED' || /timeout/i.test(error.message || '')) {
    return timeoutFallback;
  }
  return fallback;
};

export const shouldUsePersonalTimetableCache = (mode, termCode, currentTermCode) => (
  mode === 'personal' && Boolean(termCode) && termCode === currentTermCode
);

const getRealtimePersonalTerm = async termCode => {
  const context = await getTimetableContext({ mode: 'personal', term_code: termCode, target_id: '' });
  const campusCode = context.campuses?.[0]?.code || '';
  const schedule = campusCode
    ? await getTimetableSchedule({
      mode: 'personal',
      term_code: termCode,
      target_id: '',
      campus_code: 'all',
      week: null,
    })
    : { courses: [], unscheduled: [], practices: [] };
  return {
    term_code: termCode,
    campuses: context.campuses || [],
    weeks: context.weeks || [],
    sections_by_campus: campusCode ? { [campusCode]: context.sections || [] } : {},
    courses: schedule.courses || [],
    unscheduled: schedule.unscheduled || [],
    practices: schedule.practices || [],
    source: 'online',
    is_fresh: true,
    cache: {},
  };
};

function TimetablePage() {
  const screens = useBreakpoint();
  const isMobile = !screens.lg;
  const timetableMemory = useResourceMemory('timetable-current-personal');
  const requestedTerm = typeof window === 'undefined'
    ? ''
    : new URLSearchParams(window.location.search).get('term') || '';
  const restored = restorePersonalTimetableMemory(timetableMemory.data, requestedTerm);
  const [mode, setMode] = useState('personal');
  const [terms, setTerms] = useState(() => restored?.terms || []);
  const [currentTermCode, setCurrentTermCode] = useState(() => restored?.currentTermCode || '');
  const [termCode, setTermCode] = useState(() => restored?.termCode || '');
  const [context, setContext] = useState(() => restored?.context || null);
  const [campusCode, setCampusCode] = useState(() => restored?.campusCode || '');
  const [viewMode, setViewMode] = useState(() => restored?.viewMode || 'week');
  const [weekNumber, setWeekNumber] = useState(() => restored?.weekNumber || null);
  const [target, setTarget] = useState(null);
  const [targetOptions, setTargetOptions] = useState([]);
  const [targetLoading, setTargetLoading] = useState(false);
  const [targetKeyword, setTargetKeyword] = useState('');
  const [targetFilters, setTargetFilters] = useState({});
  const [targetFilterDraft, setTargetFilterDraft] = useState({});
  const [targetFilterOpen, setTargetFilterOpen] = useState(false);
  const [targetFilterOptions, setTargetFilterOptions] = useState({});
  const [targetFilterRelations, setTargetFilterRelations] = useState([]);
  const [targetFilterOptionsLoading, setTargetFilterOptionsLoading] = useState(false);
  const [targetFilterOptionsError, setTargetFilterOptionsError] = useState('');
  const [targetPreviewOptions, setTargetPreviewOptions] = useState([]);
  const [targetPreviewKeyword, setTargetPreviewKeyword] = useState('');
  const [targetPreviewPage, setTargetPreviewPage] = useState(0);
  const [targetPreviewTotal, setTargetPreviewTotal] = useState(0);
  const [targetPreviewLoading, setTargetPreviewLoading] = useState(false);
  const [targetPreviewError, setTargetPreviewError] = useState('');
  const [personalPayload, setPersonalPayload] = useState(() => restored?.personalPayload || null);
  const [schedule, setSchedule] = useState(() => restored?.schedule || null);
  const [loading, setLoading] = useState(() => !restored?.schedule);
  const [error, setError] = useState(null);
  const [autoNotice, setAutoNotice] = useState('');
  const [detailCourse, setDetailCourse] = useState(null);
  const [mobileDay, setMobileDay] = useState(todayWeekday());
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);
  const [filterDraft, setFilterDraft] = useState({ termCode: '', campusCode: '', viewMode: 'week' });
  const [contextRetry, setContextRetry] = useState(0);
  const contextGeneration = useRef(0);
  const scheduleGeneration = useRef(0);
  const targetGeneration = useRef(0);
  const targetFilterGeneration = useRef(0);
  const targetPreviewGeneration = useRef(0);
  const termsGeneration = useRef(0);
  const termsRequestRef = useRef(null);
  const personalGeneration = useRef(0);
  const autoDefaultResolved = useRef(false);
  const targetTimer = useRef(null);
  const targetFilterTimer = useRef(null);
  const targetFilterOptionsLoadedFor = useRef('');
  const personalRefreshTimer = useRef(null);
  const personalUpdateModal = useRef(null);
  const timetableViewState = useRef({ termCode: '', campusCode: '', weekNumber: null });
  const mobileDayTerm = useRef('');
  const targetSelectRef = useRef(null);
  const targetSearchState = useRef({ keyword: '', page: 0, total: 0, loading: false, requestKey: '' });
  const modeSessions = useRef(createModeSessions());
  const deepLink = useRef((() => {
    if (typeof window === 'undefined') return { term: '', week: null, day: null };
    const params = new URLSearchParams(window.location.search);
    const week = Number(params.get('week'));
    const day = Number(params.get('day'));
    return {
      term: params.get('term') || '',
      week: Number.isInteger(week) && week >= 1 && week <= 30 ? week : null,
      day: Number.isInteger(day) && day >= 1 && day <= 7 ? day : null,
    };
  })());

  const loadTerms = useCallback(() => {
    if (termsRequestRef.current) return termsRequestRef.current;
    const generation = ++termsGeneration.current;
    setLoading(true);
    setError(null);
    const request = (async () => {
      try {
        const payload = await getTimetableTerms();
        if (generation !== termsGeneration.current) return;
        const rows = payload.terms || [];
        const detectedCurrent = selectEffectiveCurrentTerm(rows, payload.current || '');
        setTerms(rows);
        setCurrentTermCode(detectedCurrent);
        const linkedTerm = rows.some(item => item.code === deepLink.current.term)
          ? deepLink.current.term
          : '';
        autoDefaultResolved.current = Boolean(linkedTerm);
        setTermCode(linkedTerm || selectDefaultTerm(rows, detectedCurrent));
        if (linkedTerm && deepLink.current.day) setMobileDay(deepLink.current.day);
      } catch (requestError) {
        if (generation !== termsGeneration.current) return;
        setError({
          stage: 'terms',
          message: requestErrorText(
            requestError,
            '无法读取课表学期，请稍后重试',
            '课表学期读取等待超时，可能有其他教务任务正在执行，请重试',
          ),
        });
        setLoading(false);
      } finally {
        if (termsRequestRef.current === request) termsRequestRef.current = null;
      }
    })();
    termsRequestRef.current = request;
    return request;
  }, []);

  useEffect(() => { loadTerms(); }, [loadTerms]);

  const setPersonalContext = useCallback((payload, nextCampusCode, nextWeek) => {
    const sectionsByCampus = payload.sections_by_campus || {};
    const sections = sectionsByCampus[nextCampusCode]
      || Object.values(sectionsByCampus).find(rows => Array.isArray(rows) && rows.length)
      || [];
    setPersonalPayload(payload);
    setContext({ campuses: payload.campuses || [], weeks: payload.weeks || [], sections });
    setCampusCode(nextCampusCode);
    setWeekNumber(nextWeek);
  }, []);

  const applyUpdatedPersonalPayload = useCallback((payload) => {
    const viewState = timetableViewState.current;
    const nextCampus = (payload.campuses || []).some(item => item.code === viewState.campusCode)
      ? viewState.campusCode
      : payload.campuses?.[0]?.code || '';
    const nextWeek = (payload.weeks || []).some(item => item.number === viewState.weekNumber)
      ? viewState.weekNumber
      : selectDefaultWeek(payload.weeks || [], { currentTerm: true });
    setPersonalContext(payload, nextCampus, nextWeek);
  }, [setPersonalContext]);

  const watchPersonalRefresh = useCallback((requestedTerm, baselineRevision, attempt = 0) => {
    if (!requestedTerm || attempt >= 8) return;
    window.clearTimeout(personalRefreshTimer.current);
    personalRefreshTimer.current = window.setTimeout(async () => {
      try {
        const payload = await getPersonalTimetable(requestedTerm, false);
        if (timetableViewState.current.termCode !== requestedTerm) return;
        const nextRevision = payload.cache?.revision || '';
        if (nextRevision && baselineRevision && nextRevision !== baselineRevision) {
          if (document.visibilityState !== 'visible') {
            applyUpdatedPersonalPayload(payload);
            return;
          }
          personalUpdateModal.current?.destroy?.();
          personalUpdateModal.current = Modal.confirm({
            title: '课表已有更新',
            content: '后台检测到当前学期课表发生变化，是否更新当前页面？',
            okText: '更新课表',
            cancelText: '稍后',
            onOk: () => applyUpdatedPersonalPayload(payload),
          });
          return;
        }
        if (payload.is_fresh === false) {
          watchPersonalRefresh(requestedTerm, baselineRevision, attempt + 1);
        }
      } catch (_error) {
        // 后台刷新失败不覆盖已显示的可用缓存，也不打断当前页面。
      }
    }, 1400);
  }, [applyUpdatedPersonalPayload]);

  useEffect(() => () => {
    window.clearTimeout(personalRefreshTimer.current);
    personalUpdateModal.current?.destroy?.();
  }, []);

  const loadPersonalTimetable = useCallback(async (requestedTerm, { refresh = false, autoDetect = false } = {}) => {
    if (!requestedTerm) return;
    const generation = ++personalGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await getPersonalTimetable(requestedTerm, refresh);
      if (generation !== personalGeneration.current) return;
      const firstCampus = payload.campuses?.[0]?.code || '';
      const linkedWeek = requestedTerm === deepLink.current.term
        && (payload.weeks || []).some(item => item.number === deepLink.current.week)
        ? deepLink.current.week
        : null;
      const defaultWeek = linkedWeek || selectDefaultWeek(payload.weeks || [], {
        currentTerm: requestedTerm === currentTermCode,
      });

      if (autoDetect && requestedTerm === currentTermCode) {
        autoDefaultResolved.current = true;
        const currentCourses = personalScheduleView(payload, firstCampus, 'week', defaultWeek).courses
          .filter(course => !course.recurrence_unknown && course.weeks?.includes(defaultWeek));
        if (!currentCourses.length) {
          const nextTermCode = immediateNextTerm(terms, requestedTerm);
          if (nextTermCode) {
            try {
              const nextPayload = await getRealtimePersonalTerm(nextTermCode);
              if (generation !== personalGeneration.current) return;
              if ((nextPayload.courses || []).length) {
                const nextCampus = nextPayload.campuses?.[0]?.code || '';
                const nextWeek = selectDefaultWeek(nextPayload.weeks || [], { currentTerm: false });
                setViewMode('term');
                setAutoNotice('当前学期处于无课周，已自动显示下一学期已发布的课表');
                setTermCode(nextTermCode);
                setPersonalContext(nextPayload, nextCampus, nextWeek);
                setLoading(false);
                return;
              }
            } catch (_nextTermError) {
              if (generation !== personalGeneration.current) return;
              setAutoNotice('下一学期课表暂无法核验，已显示本学期完整课表');
            }
          }
          setViewMode('term');
          setAutoNotice(previous => previous || '当前处于无课周，已自动显示本学期完整课表');
        } else {
          setViewMode('week');
          setAutoNotice('已自动定位到当前教学周');
        }
      }

      setPersonalContext(payload, firstCampus, defaultWeek);
      if (!refresh && payload.is_fresh === false) {
        watchPersonalRefresh(requestedTerm, payload.cache?.revision || '');
      }
      if (!firstCampus) setError({ stage: 'personal', message: '该学期没有可查询的开课校区' });
    } catch (requestError) {
      if (generation !== personalGeneration.current) return;
      setError({ stage: 'personal', message: requestErrorText(requestError, '无法读取我的课表，请稍后重试') });
    } finally {
      if (generation === personalGeneration.current) setLoading(false);
    }
  }, [currentTermCode, setPersonalContext, terms, watchPersonalRefresh]);

  useEffect(() => {
    if (!shouldUsePersonalTimetableCache(mode, termCode, currentTermCode)) return;
    loadPersonalTimetable(termCode, { autoDetect: !autoDefaultResolved.current });
  }, [currentTermCode, loadPersonalTimetable, mode, termCode]);

  const saveModeSession = useCallback((next = {}) => {
    if (mode === 'personal') return;
    modeSessions.current[mode] = {
      target,
      options: targetOptions,
      search: {
        keyword: targetSearchState.current.keyword,
        page: targetSearchState.current.page,
        total: targetSearchState.current.total,
      },
      filters: targetFilters,
      filterOptions: targetFilterOptions,
      filterRelations: targetFilterRelations,
      filterOptionsLoadedFor: targetFilterOptionsLoadedFor.current,
      ...next,
    };
  }, [mode, target, targetFilterOptions, targetFilterRelations, targetFilters, targetOptions]);

  const loadTargetFilterOptions = useCallback(async ({ force = false } = {}) => {
    if (mode === 'personal' || !termCode || targetFilterOptionsLoading) return;
    const catalogKey = `${mode}:${termCode}`;
    if (!force && targetFilterOptionsLoadedFor.current === catalogKey) return;
    const generation = ++targetFilterGeneration.current;
    setTargetFilterOptionsLoading(true);
    setTargetFilterOptionsError('');
    try {
      const payload = await getTimetableTargetFilterOptions({ mode, term_code: termCode });
      if (generation !== targetFilterGeneration.current) return;
      const options = payload.options || {};
      const relations = payload.relations || [];
      targetFilterOptionsLoadedFor.current = catalogKey;
      setTargetFilterOptions(options);
      setTargetFilterRelations(relations);
      modeSessions.current[mode] = {
        ...modeSessions.current[mode],
        filterOptions: options,
        filterRelations: relations,
        filterOptionsLoadedFor: catalogKey,
      };
    } catch (requestError) {
      if (generation !== targetFilterGeneration.current) return;
      setTargetFilterOptionsError(requestErrorText(requestError, '无法读取完整分类目录，请重试'));
    } finally {
      if (generation === targetFilterGeneration.current) setTargetFilterOptionsLoading(false);
    }
  }, [mode, targetFilterOptionsLoading, termCode]);

  const openTargetFilters = () => {
    setTargetFilterDraft(targetFilters);
    setTargetPreviewKeyword(targetKeyword);
    setTargetPreviewOptions([]);
    setTargetPreviewPage(0);
    setTargetPreviewTotal(0);
    setTargetPreviewLoading(true);
    setTargetPreviewError('');
    setTargetFilterOpen(true);
    if (targetFilterOptionsLoadedFor.current !== `${mode}:${termCode}`) loadTargetFilterOptions();
  };

  const closeTargetFilters = () => {
    clearTimeout(targetFilterTimer.current);
    targetPreviewGeneration.current += 1;
    setTargetFilterOpen(false);
    setTargetFilterDraft(targetFilters);
    setTargetPreviewLoading(false);
    setTargetPreviewError('');
  };

  const searchTargetPreview = useCallback(async (keyword = '', options = {}) => {
    if (mode === 'personal' || !termCode) return;
    const normalizedKeyword = keyword.trim();
    const page = options.page || 1;
    const append = Boolean(options.append);
    const filters = options.filters || {};
    const generation = ++targetPreviewGeneration.current;
    setTargetPreviewLoading(true);
    setTargetPreviewError('');
    if (!append) {
      setTargetPreviewOptions([]);
      setTargetPreviewPage(0);
      setTargetPreviewTotal(0);
    }
    try {
      const payload = await searchTimetableTargets({
        mode,
        term_code: termCode,
        keyword: normalizedKeyword,
        page,
        page_size: 50,
        filters,
      });
      if (generation !== targetPreviewGeneration.current) return;
      const rows = payload.items || [];
      setTargetPreviewOptions(previous => mergeTargetOptions(append ? previous : [], rows));
      setTargetPreviewPage(payload.page || page);
      setTargetPreviewTotal(payload.total || 0);
    } catch (requestError) {
      if (generation !== targetPreviewGeneration.current) return;
      setTargetPreviewError(requestErrorText(requestError, '筛选预览加载失败，请重试'));
    } finally {
      if (generation === targetPreviewGeneration.current) setTargetPreviewLoading(false);
    }
  }, [mode, termCode]);

  const searchTargets = useCallback(async (keyword = '', options = {}) => {
    if (mode === 'personal' || !termCode) return;
    const normalizedKeyword = keyword.trim();
    const page = options.page || 1;
    const append = Boolean(options.append);
    const filters = options.filters ?? targetFilters;
    const requestKey = JSON.stringify([mode, termCode, normalizedKeyword, filters, page]);
    if (targetSearchState.current.loading && targetSearchState.current.requestKey === requestKey) return;
    const generation = ++targetGeneration.current;
    targetSearchState.current = {
      ...targetSearchState.current,
      keyword: normalizedKeyword,
      loading: true,
      requestKey,
    };
    setError(previous => previous?.stage === 'targets' ? null : previous);
    setTargetLoading(true);
    try {
      const payload = await searchTimetableTargets({
        mode,
        term_code: termCode,
        keyword: normalizedKeyword,
        page,
        page_size: 50,
        filters,
      });
      if (generation !== targetGeneration.current) return;
      const rows = payload.items || [];
      setTargetOptions(previous => {
        const merged = mergeTargetOptions(append ? previous : [], rows);
        modeSessions.current[mode] = {
          ...modeSessions.current[mode],
          options: merged,
          search: { keyword: normalizedKeyword, page: payload.page || page, total: payload.total || 0 },
          filters,
        };
        return merged;
      });
      targetSearchState.current = {
        keyword: normalizedKeyword,
        page: payload.page || page,
        total: payload.total || 0,
        loading: false,
        requestKey: '',
      };
    } catch (requestError) {
      if (generation !== targetGeneration.current) return;
      if (!append) setTargetOptions([]);
      targetSearchState.current = { ...targetSearchState.current, loading: false, requestKey: '' };
      setError({ stage: 'targets', message: requestErrorText(requestError, '搜索查询对象失败，请重试') });
    } finally {
      if (generation === targetGeneration.current) setTargetLoading(false);
    }
  }, [mode, targetFilters, termCode]);

  useEffect(() => {
    if (mode !== 'personal' && termCode && !targetOptions.length) searchTargets(targetKeyword);
  }, [mode, searchTargets, targetKeyword, targetOptions.length, termCode]);

  useEffect(() => {
    if (!targetFilterOpen || mode === 'personal' || !termCode) return undefined;
    clearTimeout(targetFilterTimer.current);
    if (capacityRangeInvalid(targetFilterDraft)) {
      setTargetPreviewOptions([]);
      setTargetPreviewPage(0);
      setTargetPreviewTotal(0);
      setTargetPreviewLoading(false);
      return undefined;
    }
    targetFilterTimer.current = setTimeout(() => {
      const filters = Object.fromEntries(Object.entries(targetFilterDraft)
        .filter(([, value]) => value !== '' && value != null));
      searchTargetPreview(targetPreviewKeyword, { page: 1, filters });
    }, 250);
    return () => clearTimeout(targetFilterTimer.current);
  }, [mode, searchTargetPreview, targetFilterDraft, targetFilterOpen, targetPreviewKeyword, termCode]);

  useEffect(() => {
    if (
      !targetFilterOpen
      || mode === 'personal'
      || !termCode
      || targetFilterOptionsLoadedFor.current === `${mode}:${termCode}`
      || targetFilterOptionsLoading
      || targetFilterOptionsError
    ) return;
    loadTargetFilterOptions();
  }, [loadTargetFilterOptions, mode, targetFilterOpen, targetFilterOptionsError, targetFilterOptionsLoading, termCode]);

  useEffect(() => () => {
    clearTimeout(targetTimer.current);
    clearTimeout(targetFilterTimer.current);
    contextGeneration.current += 1;
    scheduleGeneration.current += 1;
    targetGeneration.current += 1;
    targetFilterGeneration.current += 1;
    targetPreviewGeneration.current += 1;
    personalGeneration.current += 1;
  }, []);

  useEffect(() => {
    if (shouldUsePersonalTimetableCache(mode, termCode, currentTermCode)) return;
    const targetId = target?.id || '';
    if (!termCode || (mode !== 'personal' && !targetId)) {
      setContext(null);
      setSchedule(null);
      setLoading(false);
      return;
    }
    const generation = ++contextGeneration.current;
    ++scheduleGeneration.current;
    setLoading(true);
    setError(null);
    setContext(null);
    setCampusCode('');
    setSchedule(null);
    getTimetableContext({ mode, term_code: termCode, target_id: targetId })
      .then(payload => {
        if (generation !== contextGeneration.current) return;
        setContext(payload);
        const firstCampus = payload.campuses?.[0]?.code || '';
        const defaultWeek = selectDefaultWeek(payload.weeks || [], {
          currentTerm: termCode === currentTermCode,
        });
        setCampusCode(firstCampus);
        setWeekNumber(defaultWeek);
        if (!firstCampus) {
          setError({ stage: 'context', message: '该学期没有可查询的开课校区' });
          setLoading(false);
        }
      })
      .catch(requestError => {
        if (generation !== contextGeneration.current) return;
        setError({ stage: 'context', message: requestErrorText(requestError, '无法读取课表查询条件') });
        setLoading(false);
      });
  }, [contextRetry, currentTermCode, mode, target, termCode]);

  const loadSchedule = useCallback(async () => {
    if (shouldUsePersonalTimetableCache(mode, termCode, currentTermCode)) {
      await loadPersonalTimetable(termCode, { refresh: true });
      return;
    }
    if (!context || !campusCode || !termCode) return;
    const generation = ++scheduleGeneration.current;
    setLoading(true);
    setError(null);
    setDetailCourse(null);
    try {
      const payload = await getTimetableSchedule({
        mode,
        term_code: termCode,
        target_id: target?.id || '',
        campus_code: campusCode,
        week: viewMode === 'week' ? weekNumber : null,
      });
      if (generation !== scheduleGeneration.current) return;
      setSchedule(payload);
    } catch (requestError) {
      if (generation !== scheduleGeneration.current) return;
      setError({ stage: 'schedule', message: requestErrorText(requestError, '课表读取失败，请稍后重试') });
    } finally {
      if (generation === scheduleGeneration.current) setLoading(false);
    }
  }, [campusCode, context, currentTermCode, loadPersonalTimetable, mode, target, termCode, viewMode, weekNumber]);

  useEffect(() => {
    if (shouldUsePersonalTimetableCache(mode, termCode, currentTermCode)) return;
    if (context && campusCode && (viewMode === 'term' || weekNumber)) loadSchedule();
  }, [campusCode, context, currentTermCode, loadSchedule, mode, termCode, viewMode, weekNumber]);

  useEffect(() => {
    if (
      !shouldUsePersonalTimetableCache(mode, termCode, currentTermCode)
      || !personalPayload
      || !campusCode
    ) return;
    const sectionsByCampus = personalPayload.sections_by_campus || {};
    const sections = sectionsByCampus[campusCode]
      || Object.values(sectionsByCampus).find(rows => Array.isArray(rows) && rows.length)
      || [];
    setContext(previous => previous ? { ...previous, sections } : previous);
    setSchedule(personalScheduleView(personalPayload, campusCode, viewMode, weekNumber));
  }, [campusCode, currentTermCode, mode, personalPayload, termCode, viewMode, weekNumber]);

  useEffect(() => {
    if (
      mode !== 'personal'
      || !currentTermCode
      || termCode !== currentTermCode
      || personalPayload?.term_code !== currentTermCode
    ) return;
    timetableMemory.publish({
      terms,
      currentTermCode,
      payload: personalPayload,
      campusCode,
      weekNumber,
      viewMode,
    });
  }, [
    campusCode,
    currentTermCode,
    mode,
    personalPayload,
    termCode,
    terms,
    timetableMemory.publish,
    viewMode,
    weekNumber,
  ]);

  const resetRemoteState = () => {
    contextGeneration.current += 1;
    scheduleGeneration.current += 1;
    personalGeneration.current += 1;
    setContext(null);
    setPersonalPayload(null);
    setCampusCode('');
    setWeekNumber(null);
    setSchedule(null);
    setDetailCourse(null);
    setError(null);
  };

  const switchMode = nextMode => {
    if (nextMode === mode) return;
    clearTimeout(targetTimer.current);
    clearTimeout(targetFilterTimer.current);
    saveModeSession();
    targetGeneration.current += 1;
    targetFilterGeneration.current += 1;
    targetPreviewGeneration.current += 1;
    setTargetLoading(false);
    setMode(nextMode);
    const saved = nextMode === 'personal' ? emptyModeSession() : modeSessions.current[nextMode];
    setTarget(saved.target);
    setTargetOptions(saved.options);
    setTargetKeyword(saved.search.keyword || '');
    setTargetFilters(saved.filters || {});
    setTargetFilterOptions(saved.filterOptions || {});
    setTargetFilterRelations(saved.filterRelations || []);
    targetFilterOptionsLoadedFor.current = saved.filterOptionsLoadedFor || '';
    setTargetFilterOptionsLoading(false);
    setTargetFilterOptionsError('');
    targetSearchState.current = { ...saved.search, loading: false };
    resetRemoteState();
  };

  const switchTerm = nextTermCode => {
    if (nextTermCode === termCode) return;
    clearTimeout(targetTimer.current);
    clearTimeout(targetFilterTimer.current);
    targetGeneration.current += 1;
    targetFilterGeneration.current += 1;
    targetPreviewGeneration.current += 1;
    setTargetLoading(false);
    modeSessions.current = preserveModeSessionsForTermChange(modeSessions.current);
    targetSearchState.current = {
      keyword: targetKeyword,
      page: 0,
      total: 0,
      loading: false,
      requestKey: '',
    };
    setTermCode(nextTermCode);
    autoDefaultResolved.current = true;
    setAutoNotice('');
    setTarget(null);
    setTargetOptions([]);
    setTargetFilterDraft(targetFilters);
    setTargetFilterOptions({});
    setTargetFilterRelations([]);
    targetFilterOptionsLoadedFor.current = '';
    setTargetFilterOptionsLoading(false);
    setTargetFilterOptionsError('');
    resetRemoteState();
  };

  const handleTargetSearch = value => {
    setTargetKeyword(value);
    clearTimeout(targetTimer.current);
    targetTimer.current = setTimeout(() => searchTargets(value, { page: 1 }), 300);
  };

  const applyTargetFilters = () => {
    if (capacityRangeInvalid(targetFilterDraft)) return;
    const cleaned = Object.fromEntries(Object.entries(targetFilterDraft).filter(([, value]) => value !== '' && value != null));
    clearTimeout(targetFilterTimer.current);
    targetPreviewGeneration.current += 1;
    setTargetFilterOpen(false);
    setTargetFilters(cleaned);
    setTargetKeyword(targetPreviewKeyword);
    setTargetOptions(targetPreviewOptions);
    targetSearchState.current = {
      keyword: targetPreviewKeyword.trim(),
      page: targetPreviewPage,
      total: targetPreviewTotal,
      loading: false,
      requestKey: '',
    };
    modeSessions.current[mode] = {
      ...modeSessions.current[mode],
      target: null,
      options: targetPreviewOptions,
      filters: cleaned,
      search: {
        keyword: targetPreviewKeyword.trim(),
        page: targetPreviewPage,
        total: targetPreviewTotal,
      },
    };
    setTarget(null);
    resetRemoteState();
  };

  const invalidateTargetPreview = () => {
    clearTimeout(targetFilterTimer.current);
    targetPreviewGeneration.current += 1;
    setTargetPreviewOptions([]);
    setTargetPreviewPage(0);
    setTargetPreviewTotal(0);
    setTargetPreviewLoading(true);
    setTargetPreviewError('');
  };

  const changeTargetFilter = (key, value) => {
    invalidateTargetPreview();
    setTargetFilterDraft(previous => updateTargetFilterDraft(
      mode,
      previous,
      key,
      value,
      targetFilterRelations,
      targetFilterOrder,
    ));
  };

  const resetTargetFilters = () => {
    invalidateTargetPreview();
    setTargetFilterDraft({});
  };

  const handleTargetPopupScroll = event => {
    const element = event.currentTarget;
    const state = targetSearchState.current;
    if (!shouldLoadMoreTargets({
      loading: state.loading,
      loaded: targetOptions.length,
      total: state.total,
      scrollTop: element.scrollTop,
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
    })) return;
    searchTargets(state.keyword, { page: state.page + 1, append: true });
  };

  const selectTargetItem = (
    selected,
    filters = targetFilters,
    availableOptions = targetOptions,
    searchMeta = null,
  ) => {
    if (!selected) return;
    clearTimeout(targetFilterTimer.current);
    targetPreviewGeneration.current += 1;
    const cleaned = Object.fromEntries(Object.entries(filters)
      .filter(([, value]) => value !== '' && value != null));
    setTargetFilters(cleaned);
    setTargetFilterDraft(cleaned);
    setTargetFilterOpen(false);
    if (searchMeta) {
      setTargetOptions(availableOptions);
      targetSearchState.current = {
        keyword: searchMeta.keyword.trim(),
        page: searchMeta.page,
        total: searchMeta.total,
        loading: false,
        requestKey: '',
      };
    }
    modeSessions.current[mode] = {
      ...modeSessions.current[mode],
      target: selected,
      options: availableOptions,
      filters: cleaned,
      ...(searchMeta ? { search: searchMeta } : {}),
    };
    resetRemoteState();
    setTarget(selected);
    setTargetKeyword('');
  };

  const selectTarget = value => {
    selectTargetItem(targetOptions.find(item => item.id === value) || null);
  };

  const clearTargetSelection = () => {
    modeSessions.current[mode] = { ...modeSessions.current[mode], target: null };
    setTarget(null);
    setTargetKeyword('');
    resetRemoteState();
    searchTargets('', { page: 1, filters: targetFilters });
  };

  const switchCampus = async nextCampusCode => {
    if (nextCampusCode === campusCode) return;
    if (shouldUsePersonalTimetableCache(mode, termCode, currentTermCode)) {
      setCampusCode(nextCampusCode);
      setDetailCourse(null);
      setError(null);
      return;
    }
    const targetId = target?.id || '';
    const generation = ++contextGeneration.current;
    scheduleGeneration.current += 1;
    setLoading(true);
    setError(null);
    setDetailCourse(null);
    try {
      const payload = await getTimetableContext({
        mode,
        term_code: termCode,
        target_id: targetId,
        campus_code: nextCampusCode,
      });
      if (generation !== contextGeneration.current) return;
      setContext(payload);
      setCampusCode(nextCampusCode);
      if (weekNumber && !payload.weeks?.some(item => item.number === weekNumber)) {
        setWeekNumber(selectDefaultWeek(payload.weeks || [], { currentTerm: termCode === currentTermCode }));
      }
    } catch (requestError) {
      if (generation !== contextGeneration.current) return;
      setError({ stage: 'context', message: requestErrorText(requestError, '无法读取所选校区的节次信息') });
    } finally {
      if (generation === contextGeneration.current) setLoading(false);
    }
  };

  const switchViewMode = nextViewMode => {
    if (nextViewMode === viewMode) return;
    scheduleGeneration.current += 1;
    setSchedule(null);
    setDetailCourse(null);
    setError(null);
    setViewMode(nextViewMode);
  };

  const switchWeek = nextWeek => {
    if (!nextWeek || nextWeek === weekNumber) return;
    scheduleGeneration.current += 1;
    setSchedule(null);
    setDetailCourse(null);
    setError(null);
    setWeekNumber(nextWeek);
  };

  const retryError = () => {
    if (error?.stage === 'terms') loadTerms();
    else if (error?.stage === 'personal') loadPersonalTimetable(termCode);
    else if (error?.stage === 'targets') searchTargets(targetSearchState.current.keyword || '');
    else if (error?.stage === 'context') setContextRetry(value => value + 1);
    else loadSchedule();
  };

  const openMobileFilters = () => {
    setFilterDraft({ termCode, campusCode, viewMode });
    setMobileFilterOpen(true);
  };

  const resetMobileFilters = () => {
    setFilterDraft({
      termCode: currentTermCode || selectDefaultTerm(terms),
      campusCode: context?.campuses?.[0]?.code || '',
      viewMode: 'week',
    });
  };

  const applyMobileFilters = () => {
    setMobileFilterOpen(false);
    if (filterDraft.termCode !== termCode) {
      if (filterDraft.viewMode !== viewMode) setViewMode(filterDraft.viewMode);
      switchTerm(filterDraft.termCode);
      return;
    }
    if (filterDraft.viewMode !== viewMode) switchViewMode(filterDraft.viewMode);
    if (filterDraft.campusCode && filterDraft.campusCode !== campusCode) switchCampus(filterDraft.campusCode);
  };

  const sections = useMemo(() => {
    if (context?.sections?.length) return context.sections;
    return Array.from({ length: 12 }, (_, index) => ({
      number: index + 1,
      name: `第${index + 1}节`,
      start_time: '',
      end_time: '',
    }));
  }, [context]);

  const coursesByDay = useMemo(() => {
    const result = Object.fromEntries(TIMETABLE_DAY_ORDER.map(day => [day, []]));
    (schedule?.courses || []).forEach(course => {
      if (result[course.weekday]) result[course.weekday].push(course);
    });
    return result;
  }, [schedule]);

  const selectedTerm = terms.find(item => item.code === termCode);
  const selectedCampus = context?.campuses?.find(item => item.code === campusCode);
  const selectedWeek = context?.weeks?.find(item => item.number === weekNumber);
  const effectiveCurrentWeekNumber = termCode === currentTermCode
    ? selectDefaultWeek(context?.weeks || [], { currentTerm: true })
    : null;
  const isShowingCurrentWeek = Boolean(
    termCode === currentTermCode
    && weekNumber === effectiveCurrentWeekNumber,
  );
  timetableViewState.current = { termCode, campusCode, weekNumber };

  useEffect(() => {
    if (!schedule || viewMode !== 'week' || mobileDayTerm.current === termCode) return;
    mobileDayTerm.current = termCode;
    const linkedDay = termCode === deepLink.current.term ? deepLink.current.day : null;
    setMobileDay(linkedDay || (isShowingCurrentWeek ? todayWeekday() : preferredMobileDay(coursesByDay)));
  }, [coursesByDay, isShowingCurrentWeek, schedule, termCode, viewMode]);
  const targetPlaceholder = mode === 'class'
    ? '搜索班级代码或名称'
    : mode === 'teacher'
      ? '搜索教工号或教师姓名'
      : '搜索教室代码或名称';
  const targetFilterCount = Object.keys(targetFilters).length;
  const targetFilterOrder = (TARGET_FILTER_DEFINITIONS[mode] || []).map(([key]) => key);
  const visibleTargetFilterDefinitions = usableTargetFilterDefinitions(
    TARGET_FILTER_DEFINITIONS[mode],
  );
  const targetFilterLabel = key => (TARGET_FILTER_DEFINITIONS[mode] || [])
    .find(([candidate]) => candidate === key)?.[1] || key;
  const targetFacetOptions = key => {
    const loaded = targetOptions.flatMap(item => {
      const value = item.filter_values?.[key];
      const detailKey = key === 'room_type' ? 'type' : key;
      const label = item.details?.[detailKey] || value;
      return value && label ? [{ value, label }] : [];
    });
    const options = facetTargetFilterOptions(
      key,
      mergeTargetFilterOptions(targetFilterOptions[key] || [], loaded),
      targetFilterRelations,
      targetFilterDraft,
      targetFilterOrder,
    );
    return key === 'grade' ? sortGradeOptionsNewestFirst(options) : options;
  };
  const targetTotal = targetSearchState.current.total || targetOptions.length;
  const targetHasMore = targetOptions.length < targetTotal;
  const targetSelector = mode !== 'personal' && (
    <div className="timetable-target-search-row">
      <Select
        ref={targetSelectRef}
        className="timetable-target-select"
        aria-label={`${MODE_LABELS[mode]}查询对象`}
        showSearch
        allowClear
        filterOption={false}
        value={target?.id}
        searchValue={targetKeyword}
        onSearch={handleTargetSearch}
        onChange={selectTarget}
        onClear={clearTargetSelection}
        onFocus={() => {
          if (!targetOptions.length && !targetSearchState.current.loading) searchTargets(targetKeyword);
        }}
        onPopupScroll={handleTargetPopupScroll}
        loading={targetLoading}
        placeholder={targetPlaceholder}
        notFoundContent={targetLoading ? '正在搜索完整名单…' : '没有匹配结果，请调整关键词或筛选条件'}
        optionRender={option => (
          <div className="timetable-target-option">
            <strong>{option.data.target.name}</strong>
            <span>{option.data.target.id}</span>
            {targetOptionSummary(option.data.target) && <small>{targetOptionSummary(option.data.target)}</small>}
          </div>
        )}
        options={targetOptions.map(item => ({ value: item.id, label: item.name, target: item }))}
      />
      <Button
        icon={<FilterOutlined />}
        className={targetFilterCount ? 'is-active' : ''}
        onClick={openTargetFilters}
      >筛选{targetFilterCount ? ` ${targetFilterCount}` : ''}</Button>
    </div>
  );

  const desktopControls = (
    <div className="timetable-controls timetable-desktop-controls" aria-label="课表查询条件">
      <label><span>学期</span><Select
        value={termCode || undefined}
        onChange={switchTerm}
        options={terms.map(item => ({
          value: item.code,
          label: `${item.name}${item.code === currentTermCode || item.current ? '（当前）' : ''}`,
        }))}
        placeholder="选择学期"
      /></label>
      {mode !== 'personal' && <label className="timetable-target-field"><span>查询对象</span>{targetSelector}</label>}
      <label><span>校区</span><Select
        value={campusCode || undefined}
        onChange={switchCampus}
        disabled={!context?.campuses?.length}
        options={(context?.campuses || []).map(item => ({ value: item.code, label: item.name }))}
        placeholder="选择校区"
      /></label>
      <label><span>显示范围</span><Segmented
        value={viewMode}
        onChange={switchViewMode}
        options={[{ label: '按周', value: 'week' }, { label: '全学期', value: 'term' }]}
      /></label>
      {viewMode === 'week' && <label><span>教学周</span><Select
        value={weekNumber}
        onChange={switchWeek}
        disabled={!context?.weeks?.length}
        options={(context?.weeks || []).map(item => ({
          value: item.number,
          label: `${item.name}${termCode === currentTermCode && item.number === effectiveCurrentWeekNumber ? '（本周）' : ''}${item.start_date ? ` · ${item.start_date.slice(5)}—${item.end_date.slice(5)}` : ''}`,
        }))}
        placeholder="选择周次"
      /></label>}
      <Space className="timetable-control-actions">
        <Tooltip title={schedule?.last_update
          ? `最后保存: ${new Date(schedule.last_update).toLocaleString('zh-CN', { hour12: false })}`
          : '点击刷新课表'}>
          <Button icon={<ReloadOutlined />} onClick={loadSchedule} disabled={!context || !campusCode} loading={loading}>刷新</Button>
        </Tooltip>
      </Space>
    </div>
  );

  const hasArrangedCourses = Boolean(schedule?.courses?.length);
  const hasOtherCourses = Boolean(schedule?.unscheduled?.length || schedule?.practices?.length);

  return (
    <div className="timetable-page">
      <Tabs activeKey={mode} onChange={switchMode} items={TIMETABLE_MODES} className="timetable-mode-tabs" />

      {isMobile ? (
        <>
          {targetSelector}
          <div className="timetable-mobile-context">
            <button type="button" className="timetable-context-copy" onClick={openMobileFilters}>
              <strong>{selectedTerm?.name || '正在识别学期'}</strong>
              <span>
                {viewMode === 'week' ? (selectedWeek?.name || '正在识别教学周') : '全学期'}
                {selectedCampus?.name ? ` · ${selectedCampus.name}` : ''}
              </span>
            </button>
            <Segmented
              aria-label="课表显示范围"
              className="timetable-mobile-view-toggle"
              value={viewMode}
              onChange={switchViewMode}
              options={[{ label: '周', value: 'week' }, { label: '总', value: 'term' }]}
            />
            <Tooltip title={schedule?.last_update
              ? `最后保存: ${new Date(schedule.last_update).toLocaleString('zh-CN', { hour12: false })}`
              : '点击刷新课表'}>
              <Button aria-label="刷新课表" icon={<ReloadOutlined />} onClick={loadSchedule} disabled={!context || !campusCode} loading={loading} />
            </Tooltip>
          </div>
          {viewMode === 'week' && context?.weeks?.length > 0 && (
            <MobileWeekTimeline
              weeks={context.weeks}
              selectedWeek={weekNumber}
              currentWeek={termCode === currentTermCode ? effectiveCurrentWeekNumber : null}
              onChange={switchWeek}
            />
          )}
        </>
      ) : desktopControls}

      {mode === 'personal' && autoNotice && (
        <Alert type="info" showIcon message={autoNotice} className="timetable-auto-notice" />
      )}

      {target && mode !== 'personal' && !isMobile && (
        <div className="timetable-target-summary">
          <strong>{target.name}</strong><span>{target.id}</span>
          {targetDescription(target) && <small>{targetDescription(target)}</small>}
        </div>
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          message={error.message}
          className="timetable-error"
          action={<Button size="small" onClick={retryError}>重试</Button>}
        />
      )}

      {loading && !schedule ? (
        <div className="timetable-loading" aria-live="polite"><Skeleton active paragraph={{ rows: isMobile ? 5 : 8 }} /></div>
      ) : mode !== 'personal' && !target ? (
        <TargetResultPanel
          items={targetOptions}
          loading={targetLoading}
          total={targetTotal}
          mode={mode}
          onSelect={selectTargetItem}
          onLoadMore={() => searchTargets(targetSearchState.current.keyword, {
            page: targetSearchState.current.page + 1,
            append: true,
          })}
        />
      ) : schedule ? (
        <>
          {hasArrangedCourses ? (
            <>
              <div className={isMobile ? '' : 'timetable-screen-mobile-hidden'}>
                <MobileTimetable
                  coursesByDay={coursesByDay}
                  selectedDay={mobileDay}
                  viewMode={viewMode}
                  currentTerm={termCode === currentTermCode}
                  currentWeekNumber={effectiveCurrentWeekNumber}
                  onDayChange={setMobileDay}
                  onCourseClick={setDetailCourse}
                />
              </div>
              <div className={isMobile ? 'timetable-screen-desktop-hidden' : ''}>
                <DesktopTimetable
                  coursesByDay={coursesByDay}
                  sections={sections}
                  viewMode={viewMode}
                  mode={mode}
                  currentTerm={termCode === currentTermCode}
                  currentWeekNumber={effectiveCurrentWeekNumber}
                  showToday={shouldHighlightToday({
                    termCode,
                    currentTermCode,
                    viewMode,
                    weekNumber,
                    currentWeekNumber: effectiveCurrentWeekNumber,
                  })}
                  onCourseClick={setDetailCourse}
                />
              </div>
            </>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={hasOtherCourses ? '当前范围内没有按节次安排的课程' : '当前条件下暂无课程安排'}
            />
          )}
          <OtherCourses schedule={schedule} />
        </>
      ) : null}

      <CourseDetail course={detailCourse} onClose={() => setDetailCourse(null)} isMobile={isMobile} />

      <Modal
        open={targetFilterOpen}
        onCancel={closeTargetFilters}
        onOk={applyTargetFilters}
        okButtonProps={{
          disabled: targetPreviewLoading
            || Boolean(targetPreviewError)
            || capacityRangeInvalid(targetFilterDraft),
        }}
        okText="应用筛选"
        cancelText="取消"
        title={`${MODE_LABELS[mode] || ''}筛选`}
        width={760}
        footer={(_, { OkBtn, CancelBtn }) => (
          <div className="timetable-target-filter-actions">
            <Button onClick={resetTargetFilters}>清空筛选</Button>
            <Space><CancelBtn /><OkBtn /></Space>
          </div>
        )}
      >
        <p className="timetable-target-filter-help">
          筛选会作用于教务系统完整名单；分类目录独立加载，不受当前滚动页限制。
          {targetFilterOptionsLoading && <span> 正在加载完整分类…</span>}
        </p>
        <label className="timetable-filter-field timetable-filter-term-field">
          <span>学期</span>
          <Select
            value={termCode || undefined}
            onChange={switchTerm}
            options={terms.map(item => ({
              value: item.code,
              label: `${item.name}${item.code === currentTermCode || item.current ? '（当前）' : ''}`,
            }))}
            placeholder="选择学期"
          />
        </label>
        {targetFilterOptionsError && (
          <Alert
            type="warning"
            showIcon
            message={targetFilterOptionsError}
            action={<Button size="small" onClick={loadTargetFilterOptions}>重试</Button>}
          />
        )}
        <Input.Search
          allowClear
          className="timetable-filter-search"
          value={targetPreviewKeyword}
          onChange={event => {
            invalidateTargetPreview();
            setTargetPreviewKeyword(event.target.value);
          }}
          onSearch={value => searchTargetPreview(value, {
            page: 1,
            filters: Object.fromEntries(Object.entries(targetFilterDraft)
              .filter(([, item]) => item !== '' && item != null)),
          })}
          placeholder={`可选：搜索${MODE_LABELS[mode]?.replace('课表', '') || '查询对象'}名称或代码`}
        />
        <div className="timetable-target-filter-grid">
          {visibleTargetFilterDefinitions.map(([key, label]) => {
            const missingParent = targetFilterMissingParent(mode, key, targetFilterDraft);
            const options = targetFacetOptions(key);
            const unavailable = !targetFilterOptionsLoading && !options.length;
            return (
              <label className="timetable-filter-field" key={key}>
                <span>{label}</span>
                <Select
                  allowClear
                  showSearch
                  disabled={Boolean(missingParent) || targetFilterOptionsLoading || unavailable}
                  value={targetFilterDraft[key] || undefined}
                  options={options}
                  onChange={value => changeTargetFilter(key, value)}
                  placeholder={missingParent
                    ? `请先选择${targetFilterLabel(missingParent)}`
                    : unavailable ? '暂无可用选项' : `选择${label}`}
                  filterOption={(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())}
                />
              </label>
            );
          })}
          {mode === 'room' && (
            <fieldset className="timetable-capacity-filter">
              <legend>容量范围</legend>
              <div>
                <InputNumber
                  min={0}
                  max={10000}
                  status={capacityRangeInvalid(targetFilterDraft) ? 'error' : undefined}
                  value={targetFilterDraft.min_capacity}
                  onChange={value => {
                    invalidateTargetPreview();
                    setTargetFilterDraft(previous => ({ ...previous, min_capacity: value }));
                  }}
                  placeholder="最少人数"
                  aria-label="最低容量"
                />
                <span>至</span>
                <InputNumber
                  min={0}
                  max={10000}
                  status={capacityRangeInvalid(targetFilterDraft) ? 'error' : undefined}
                  value={targetFilterDraft.max_capacity}
                  onChange={value => {
                    invalidateTargetPreview();
                    setTargetFilterDraft(previous => ({ ...previous, max_capacity: value }));
                  }}
                  placeholder="最多人数"
                  aria-label="最高容量"
                />
              </div>
              {capacityRangeInvalid(targetFilterDraft) && <small>最低容量不能大于最高容量</small>}
            </fieldset>
          )}
          <label className="timetable-filter-field">
            <span>排课状态</span>
            <Select
              allowClear
              value={targetFilterDraft.has_schedule}
              onChange={value => {
                invalidateTargetPreview();
                setTargetFilterDraft(previous => ({ ...previous, has_schedule: value }));
              }}
              placeholder="全部"
              options={[{ value: 'yes', label: '已有排课' }, { value: 'no', label: '尚无排课' }]}
            />
          </label>
        </div>
        {targetPreviewError && (
          <Alert
            type="warning"
            showIcon
            message={targetPreviewError}
            action={<Button size="small" onClick={() => searchTargetPreview(targetPreviewKeyword, {
              page: 1,
              filters: Object.fromEntries(Object.entries(targetFilterDraft)
                .filter(([, value]) => value !== '' && value != null)),
            })}>重试</Button>}
          />
        )}
        <TargetResultPanel
          compact
          items={targetPreviewOptions}
          loading={targetPreviewLoading}
          total={targetPreviewTotal}
          mode={mode}
          onSelect={item => selectTargetItem(
            item,
            targetFilterDraft,
            targetPreviewOptions,
            {
              keyword: targetPreviewKeyword.trim(),
              page: targetPreviewPage,
              total: targetPreviewTotal,
            },
          )}
          onLoadMore={() => searchTargetPreview(targetPreviewKeyword, {
            page: targetPreviewPage + 1,
            append: true,
            filters: Object.fromEntries(Object.entries(targetFilterDraft)
              .filter(([, value]) => value !== '' && value != null)),
          })}
        />
      </Modal>

      <MobileFilterDrawer
        open={mobileFilterOpen}
        onClose={() => setMobileFilterOpen(false)}
        onApply={applyMobileFilters}
        onReset={resetMobileFilters}
        title="课表条件"
      >
        <label className="timetable-filter-field"><span>学期</span><Select
          value={filterDraft.termCode || undefined}
          onChange={value => setFilterDraft(previous => ({ ...previous, termCode: value, campusCode: '' }))}
          options={terms.map(item => ({
            value: item.code,
            label: `${item.name}${item.code === currentTermCode || item.current ? '（当前）' : ''}`,
          }))}
        /></label>
        <label className="timetable-filter-field"><span>校区</span><Select
          value={filterDraft.campusCode || undefined}
          onChange={value => setFilterDraft(previous => ({ ...previous, campusCode: value }))}
          disabled={filterDraft.termCode !== termCode || !context?.campuses?.length}
          placeholder={filterDraft.termCode !== termCode ? '切换学期后自动识别' : '选择校区'}
          options={(context?.campuses || []).map(item => ({ value: item.code, label: item.name }))}
        /></label>
        <label className="timetable-filter-field"><span>显示范围</span><Segmented
          block
          value={filterDraft.viewMode}
          onChange={value => setFilterDraft(previous => ({ ...previous, viewMode: value }))}
          options={[{ label: '按周', value: 'week' }, { label: '全学期', value: 'term' }]}
        /></label>
      </MobileFilterDrawer>
    </div>
  );
}

function DesktopTimetable({
  coursesByDay,
  sections,
  viewMode,
  mode,
  currentTerm,
  currentWeekNumber,
  showToday,
  onCourseClick,
}) {
  const [expandedCluster, setExpandedCluster] = useState(null);
  const [activeClusterCourse, setActiveClusterCourse] = useState(null);
  const sectionHeights = adaptiveSectionHeights(sections, coursesByDay, viewMode, mode);
  const sectionOffsets = sectionHeights.reduce((offsets, value) => (
    [...offsets, offsets[offsets.length - 1] + value]
  ), [0]);
  const totalHeight = sectionOffsets[sectionOffsets.length - 1] || 64;
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const today = showToday ? todayWeekday(now) : null;
  const layoutsByDay = Object.fromEntries(
    TIMETABLE_DAY_ORDER.map(day => [day, groupDayCourses(coursesByDay[day])]),
  );
  const gridStyle = {
    gridTemplateColumns: '72px repeat(7, minmax(0, 1fr))',
    width: '100%',
  };
  return (
    <>
    <section className="timetable-desktop" aria-label="周课表">
      <div className="timetable-grid-header" style={gridStyle}>
        <div className="timetable-axis-heading">节次</div>
        {TIMETABLE_DAY_ORDER.map(day => {
          const name = WEEKDAY_NAMES[day - 1];
          return <div className={today === day ? 'is-today' : ''} key={day}>{name}{today === day && <small>今天</small>}</div>;
        })}
      </div>
      <div className="timetable-grid-body" style={{ ...gridStyle, height: totalHeight }}>
        <div className="timetable-section-axis">
          {sections.map((section, sectionIndex) => (
            <div className="timetable-section-label" key={section.number} style={{ height: sectionHeights[sectionIndex] }}>
              <strong>{section.name}</strong>
              {(section.start_time || section.end_time) && <span>{section.start_time}{section.end_time ? `–${section.end_time}` : ''}</span>}
            </div>
          ))}
        </div>
        {TIMETABLE_DAY_ORDER.map(day => {
          const name = WEEKDAY_NAMES[day - 1];
          return (
          <div className={`timetable-day-column${today === day ? ' is-today' : ''}`} key={day} aria-label={name}>
            {sectionOffsets.slice(1, -1).map((offset, lineIndex) => (
              <span className="timetable-section-grid-line" style={{ top: offset }} key={`line-${lineIndex}`} aria-hidden="true" />
            ))}
            {layoutsByDay[day].map((group, groupIndex) => {
              const startIndex = Math.min(Math.max(Number(group.start_section || 1) - 1, 0), sectionHeights.length - 1);
              const endIndex = Math.min(Math.max(Number(group.end_section || group.start_section || 1), startIndex + 1), sectionHeights.length);
              const top = sectionOffsets[startIndex] + 3;
              const height = sectionOffsets[endIndex] - sectionOffsets[startIndex] - 6;
              if (group.courses.length === 1) {
                const course = group.courses[0];
                const content = courseCardContent(course);
                const happeningNow = isCourseHappeningNow(course, { now, currentTerm, currentWeekNumber });
                const contextText = courseContextText(course, mode);
                const sectionText = `第${course.start_section}${course.end_section !== course.start_section ? `–${course.end_section}` : ''}节`;
                return (
                  <button
                    type="button"
                    key={`${course.id}-${course.start_section}-${groupIndex}`}
                    className={`timetable-course-block${course.hasActualConflict ? ' has-conflict' : ''}${happeningNow ? ' is-course-now' : ''}`}
                    style={{ top, height, left: 3, width: 'calc(100% - 6px)', '--course-color': course.color }}
                    onClick={() => onCourseClick(course)}
                    aria-label={`${content.name}，${content.location}，${name}${sectionText}${viewMode === 'term' ? `，${formatWeekNumbers(course.weeks) || '周次待确认'}` : ''}`}
                  >
                    <strong className="course-title">{content.name}</strong>
                    {viewMode === 'term' && <span className="course-weeks">{formatWeekNumbers(course.weeks) || '周次待确认'}</span>}
                    <span className="course-location"><EnvironmentOutlined /> {content.location}</span>
                    {contextText && <span className="course-context">{contextText}</span>}
                    <span className="course-secondary">{uniqueTexts([content.type, sectionText]).join(' · ')}</span>
                  </button>
                );
              }
              const metrics = clusterLayoutMetrics(
                height,
                group.courses.length,
                group.courses.map(course => estimatedFoldedCourseHeight(course, viewMode)),
              );
              const { hasHiddenCourses } = metrics;
              const visibleCourses = group.courses.slice(0, metrics.visibleCourseCount);
              const groupKey = `${day}-${group.start_section}-${group.end_section}-${groupIndex}`;
              const requestedActiveIndex = activeClusterCourse?.groupKey === groupKey
                ? activeClusterCourse.courseIndex
                : 0;
              const activeIndex = requestedActiveIndex >= 0 && requestedActiveIndex < visibleCourses.length
                ? requestedActiveIndex
                : 0;
              const stackLayout = clusterStackLayout(
                metrics,
                visibleCourses.length,
                activeIndex,
                hasHiddenCourses,
              );
              return (
                <div
                  className="timetable-course-cluster"
                  style={{ top, height }}
                  key={`cluster-${group.start_section}-${group.end_section}-${groupIndex}`}
                  role="group"
                  aria-label={`${name}第${group.start_section}至${group.end_section}节，共${group.courses.length}项安排`}
                  onMouseMove={event => {
                    const localY = event.clientY - event.currentTarget.getBoundingClientRect().top;
                    const hovered = stackLayout.courses.slice().reverse().find(item => (
                      localY >= item.top && localY <= item.top + item.height
                    ));
                    if (hovered && hovered.courseIndex !== activeIndex) {
                      setActiveClusterCourse({ groupKey, courseIndex: hovered.courseIndex });
                    }
                  }}
                  onMouseLeave={() => setActiveClusterCourse({ groupKey, courseIndex: 0 })}
                  onClick={event => {
                    const localY = event.clientY - event.currentTarget.getBoundingClientRect().top;
                    const selected = stackLayout.courses.slice().reverse().find(item => (
                      localY >= item.top && localY <= item.top + item.height
                    ));
                    if (selected) {
                      onCourseClick(visibleCourses[selected.courseIndex]);
                    } else if (
                      stackLayout.more
                      && localY >= stackLayout.more.top
                      && localY <= stackLayout.more.top + stackLayout.more.height
                    ) {
                      setExpandedCluster({ dayName: name, ...group });
                    }
                  }}
                >
                  {visibleCourses.map((course, courseIndex) => {
                    const content = courseCardContent(course);
                    const happeningNow = isCourseHappeningNow(course, { now, currentTerm, currentWeekNumber });
                    const contextText = courseContextText(course, mode);
                    const sectionText = `第${course.start_section}${course.end_section !== course.start_section ? `–${course.end_section}` : ''}节`;
                    const isExpanded = courseIndex === activeIndex;
                    const itemLayout = stackLayout.courses[courseIndex];
                    const firstLowerItem = isExpanded
                      ? stackLayout.courses.slice(courseIndex + 1).find(Boolean) || stackLayout.more
                      : null;
                    const lowerReserve = firstLowerItem
                      ? Math.max(itemLayout.top + itemLayout.height - firstLowerItem.top, 0)
                      : 0;
                    return (
                      <button
                        type="button"
                        className={`timetable-cluster-stack-card${isExpanded ? ' is-expanded' : ' is-folded'}${course.hasActualConflict ? ' has-conflict' : ''}${happeningNow ? ' is-course-now' : ''}`}
                        style={{
                          top: itemLayout.top,
                          height: itemLayout.height,
                          '--course-color': course.color,
                          '--cluster-lower-reserve': `${lowerReserve}px`,
                        }}
                        key={`${course.id}-${courseIndex}`}
                        onClick={event => {
                          event.stopPropagation();
                          onCourseClick(course);
                        }}
                        onFocus={() => setActiveClusterCourse({ groupKey, courseIndex })}
                        aria-expanded={isExpanded}
                        aria-label={`${content.name}，${sectionText}，${viewMode === 'term' ? formatWeekNumbers(course.weeks) : content.location}${course.hasActualConflict ? '，同周时间冲突' : ''}，悬停展开，点击查看详情`}
                      >
                        <strong>{content.name}</strong>
                        <span className={`timetable-cluster-stack-summary${viewMode === 'term' ? ' is-week-text' : ''}`}>{viewMode === 'term'
                          ? formatWeekNumbers(course.weeks) || '周次待确认'
                          : sectionText}</span>
                        <span className="timetable-cluster-stack-detail">{sectionText}</span>
                        <span className="timetable-cluster-stack-detail"><EnvironmentOutlined /> {content.location}</span>
                        {contextText && <span className="timetable-cluster-stack-detail">{contextText}</span>}
                        {content.type && <span className="timetable-cluster-stack-detail">{content.type}</span>}
                        {course.hasActualConflict && <small>{isExpanded ? '同周时间冲突' : '冲突'}</small>}
                      </button>
                    );
                  })}
                  {hasHiddenCourses && (
                    <button
                      type="button"
                      className="timetable-cluster-more"
                      style={{
                        top: stackLayout.more.top,
                        height: stackLayout.more.height,
                      }}
                      onClick={event => {
                        event.stopPropagation();
                        setExpandedCluster({ dayName: name, ...group });
                      }}
                    >
                      另 {group.courses.length - visibleCourses.length} 项 · 查看全部
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          );
        })}
      </div>
    </section>
    <Modal
      open={Boolean(expandedCluster)}
      title={`${expandedCluster?.dayName || ''}时段内全部安排`}
      footer={null}
      onCancel={() => setExpandedCluster(null)}
      width={560}
    >
      <div className="timetable-cluster-dialog-list">
        {(expandedCluster?.courses || []).map((course, index) => {
          const content = courseCardContent(course);
          const sectionText = `第${course.start_section}${course.end_section !== course.start_section ? `–${course.end_section}` : ''}节`;
          return (
            <button
              type="button"
              className="timetable-cluster-dialog-item"
              key={`${course.id}-${index}`}
              onClick={() => { setExpandedCluster(null); onCourseClick(course); }}
            >
              <strong>{content.name}</strong>
              <span>{sectionText} · {formatWeekNumbers(course.weeks) || '周次待确认'}</span>
              <span>{content.location}</span>
            </button>
          );
        })}
      </div>
    </Modal>
    </>
  );
}

function MobileWeekTimeline({ weeks, selectedWeek, currentWeek, onChange }) {
  const railRef = useRef(null);
  const settleTimer = useRef(null);
  const programmaticScroll = useRef(false);

  useEffect(() => {
    const active = railRef.current?.querySelector(`[data-week="${selectedWeek}"]`);
    if (!active) return undefined;
    programmaticScroll.current = true;
    active.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    const timer = window.setTimeout(() => { programmaticScroll.current = false; }, 420);
    return () => window.clearTimeout(timer);
  }, [selectedWeek]);

  useEffect(() => () => window.clearTimeout(settleTimer.current), []);

  const selectCenteredWeek = () => {
    if (programmaticScroll.current || !railRef.current) return;
    const rail = railRef.current;
    const center = rail.getBoundingClientRect().left + rail.clientWidth / 2;
    const items = [...rail.querySelectorAll('[data-week]')];
    const nearest = items.reduce((best, item) => {
      const rect = item.getBoundingClientRect();
      const distance = Math.abs(rect.left + rect.width / 2 - center);
      return !best || distance < best.distance ? { item, distance } : best;
    }, null);
    const nextWeek = Number(nearest?.item?.dataset.week);
    if (nextWeek && nextWeek !== selectedWeek) onChange(nextWeek);
  };

  return (
    <div
      className="timetable-week-timeline"
      ref={railRef}
      role="listbox"
      aria-label="左右滑动选择教学周"
      onScroll={() => {
        window.clearTimeout(settleTimer.current);
        settleTimer.current = window.setTimeout(selectCenteredWeek, 160);
      }}
    >
      {weeks.map(week => (
        <button
          type="button"
          role="option"
          aria-selected={week.number === selectedWeek}
          className={week.number === selectedWeek ? 'is-selected' : ''}
          data-week={week.number}
          key={week.number}
          onClick={() => onChange(week.number)}
        >
          <strong>{week.name}</strong>
          <span>{week.start_date ? `${week.start_date.slice(5)}—${week.end_date.slice(5)}` : '日期待定'}</span>
          {week.number === currentWeek && <small>本周</small>}
        </button>
      ))}
    </div>
  );
}

function MobileTimetable({
  coursesByDay,
  selectedDay,
  viewMode,
  currentTerm,
  currentWeekNumber,
  onDayChange,
  onCourseClick,
}) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const courses = [...(coursesByDay[selectedDay] || [])].sort((a, b) => a.start_section - b.start_section);
  return (
    <section className="timetable-mobile" aria-label="手机课表">
      <Segmented
        block
        value={selectedDay}
        onChange={onDayChange}
        options={TIMETABLE_DAY_ORDER.map(day => ({
          label: <span className="timetable-day-option"><span>周{SHORT_WEEKDAY_NAMES[day - 1]}</span><small>{coursesByDay[day].length || ''}</small></span>,
          value: day,
        }))}
      />
      <div className="timetable-mobile-list">
        {courses.length ? courses.map((course, index) => {
          const content = courseCardContent(course);
          const happeningNow = isCourseHappeningNow(course, { now, currentTerm, currentWeekNumber });
          return (
            <Card
              key={`${course.id}-${course.start_section}-${index}`}
              size="small"
              className={`timetable-mobile-card${happeningNow ? ' is-course-now' : ''}`}
              style={{ '--course-color': course.color }}
              onClick={() => onCourseClick(course)}
              role="button"
              tabIndex={0}
              onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onCourseClick(course);
                }
              }}
            >
              <div className="mobile-course-time">
                <strong>第{course.start_section}–{course.end_section}节</strong>
                {(course.start_time || course.end_time) && <span>{course.start_time || '?'}–{course.end_time || '?'}</span>}
              </div>
              <strong className="mobile-course-title">{content.name}</strong>
              {viewMode === 'term' && <span className="mobile-course-weeks">{formatWeekNumbers(course.weeks) || '周次待确认'}</span>}
              <span className="mobile-course-location"><EnvironmentOutlined /> {content.location}</span>
              {content.type && <span className="mobile-course-type">{content.type}</span>}
            </Card>
          );
        }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这一天没有课程，可切换其他日期" />}
      </div>
    </section>
  );
}

function OtherCourses({ schedule }) {
  const groups = [
    { key: 'unscheduled', title: '未安排节次', description: '课程存在，但暂未给出固定上课节次', rows: schedule.unscheduled || [] },
    { key: 'practices', title: '集中实践', description: '通常按连续日期或集中周安排', rows: schedule.practices || [] },
  ].filter(group => group.rows.length);
  if (!groups.length) return null;
  return (
    <section className="timetable-other-courses" aria-label="其他课程安排">
      {groups.map(group => (
        <Card key={group.key} title={group.title} extra={<span>{group.rows.length} 门</span>} size="small">
          <p className="timetable-other-description">{group.description}</p>
          {group.rows.map((course, index) => (
            <div className="timetable-other-row" key={`${course.course_code}-${course.course_name}-${index}`}>
              <strong>{course.course_name}</strong>
              {course.course_code && <span>{course.course_code}</span>}
              {(course.details || []).length > 0 && <span>{course.details.join(' · ')}</span>}
            </div>
          ))}
        </Card>
      ))}
    </section>
  );
}

function CourseDetailContent({ course }) {
  if (!course) return null;
  const content = courseCardContent(course);
  const details = uniqueTexts(course.title_details?.length ? course.title_details : course.cell_details || []);
  const repeatedTags = new Set([
    course.course_nature,
    course.course_type,
    course.assessment_type,
    course.grading_scheme,
  ].filter(Boolean));
  const detailTags = uniqueTexts(course.tags || []).filter(tag => !repeatedTags.has(tag));
  return (
    <div className="timetable-course-detail">
      <div className="timetable-detail-lead">
        <span>{WEEKDAY_NAMES[course.weekday - 1] || '未安排'} · 第{course.start_section}–{course.end_section}节</span>
        <strong><EnvironmentOutlined /> {content.location}</strong>
        {content.type && <Tag>{content.type}</Tag>}
      </div>
      <Descriptions size="small" column={1} colon={false}>
        {(course.start_time || course.end_time) && <Descriptions.Item label="具体时间">{course.start_time || '?'}–{course.end_time || '?'}</Descriptions.Item>}
        <Descriptions.Item label="教师">{course.teachers?.join('、') || '详见官方安排'}</Descriptions.Item>
        <Descriptions.Item label="班级">{course.classes?.join('、') || '—'}</Descriptions.Item>
        <Descriptions.Item label="课程代码">{course.course_code || '—'}</Descriptions.Item>
        <Descriptions.Item label="课程性质">{course.course_nature || course.course_type || '未提供'}</Descriptions.Item>
        <Descriptions.Item label="考核方式">{course.assessment_type || '未提供'}</Descriptions.Item>
        {course.grading_scheme && <Descriptions.Item label="成绩类型">{course.grading_scheme}</Descriptions.Item>}
        <Descriptions.Item label="上课周次">{formatWeekNumbers(course.weeks) || '周次待确认'}</Descriptions.Item>
        {detailTags.length > 0 && <Descriptions.Item label="课程标记"><Space size={[4, 4]} wrap>{detailTags.map(tag => <Tag key={tag}>{tag}</Tag>)}</Space></Descriptions.Item>}
      </Descriptions>
      {details.length > 0 && (
        <section className="timetable-detail-official">
          <strong>完整安排</strong>
          {details.map((line, index) => <span key={`${line}-${index}`}>{line}</span>)}
        </section>
      )}
    </div>
  );
}

function CourseDetail({ course, onClose, isMobile }) {
  const title = course?.course_name || '课程详情';
  if (isMobile) {
    return (
      <MobileDetailDrawer open={Boolean(course)} onClose={onClose} title={title}>
        <CourseDetailContent course={course} />
      </MobileDetailDrawer>
    );
  }
  return (
    <Modal open={Boolean(course)} onCancel={onClose} footer={null} title={title} width={560}>
      <CourseDetailContent course={course} />
    </Modal>
  );
}

export default TimetablePage;
