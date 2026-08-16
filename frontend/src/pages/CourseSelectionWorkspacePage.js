import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Card, Checkbox, Descriptions, Empty, Input, InputNumber, Modal,
  Pagination, Segmented, Select, Space, Spin, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  ArrowLeftOutlined, CalendarOutlined, CheckCircleOutlined,
  BookOutlined,
  PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined, RobotOutlined,
  SearchOutlined, ShoppingCartOutlined, SwapOutlined,
} from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import {
  actionJwxkAutomationTask, applyJwxkWeights, createJwxkAutomationTask,
  checkJwxkCatalogEligibility,
  deselectJwxkCourse, getJwxkCatalogDetail, getJwxkCatalogFilterOptions,
  getJwxkSelected, getJwxkStatus, getJwxkWeightBudget,
  getJwxkWeightConfig,
  listJwxkAutomationTasks, planJwxkWeights, previewJwxkPlan, readJwxkPlan,
  saveJwxkPlan, searchJwxkCatalog, selectJwxkCourse,
} from '../services/api';
import TimetablePage from './TimetablePage';
import {
  applyCatalogDisplayLayout,
  catalogAvailabilityRequestMode,
  catalogAvailabilityRemoteFilters,
  catalogGroupLiveStats,
  catalogGroupsForDisplay,
  createCatalogDisplayLayout,
  extendCatalogDisplayLayout,
  filterAcademicPlanGapsForBatch,
  findMatchingSelectionRecord,
  immediateSelectionConflictMap,
  isCurrentBatchSelectionRecord,
  academicPlanSelectionRecords,
  matchAcademicGapCatalogFilters,
  mergeCatalogRefreshPreservingOrder,
  mergeCatalogFilterLayers,
  patchCatalogSelection,
  removeSelectionRecord,
  sameSelectionCourse,
  selectionParticipantCount,
  selectionParticipantLabel,
  summarizeSelectionConflictsByClass,
  toggleCatalogPreviewCourse,
  uniqueDisplayLabels,
  upsertSelectionRecord,
} from '../utils/jwxkSchedule';
import {
  collectAcademicPlanDeficits,
  getAcademicRuleDeficitText,
  overlayExternalSelectedCourses,
} from '../utils/academicReport';
import { useCachedResource } from '../resources/ResourceStore';
import CourseOutlineDrawer from '../components/CourseOutlineDrawer';
import './CourseSelectionPage.css';

const { Paragraph, Text, Title } = Typography;
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];
const CATALOG_CAPACITY_REFRESH_MS = 30_000;
const TASK_STATUS_REFRESH_MS = 1_000;
const formatTaskTimestamp = value => value
  ? new Date(value).toLocaleString('zh-CN', { hour12: false })
  : '尚未执行';
const COURSE_SCOPE_LABELS = {
  ALL: '所有课程', ROUND: '本轮课程', TJKC: '任务推荐班课程',
  FANKC: '培养方案内课', FAWKC: '培养方案外课程', XGKC: '通识选修课',
  CXKC: '重修课程', TYKC: '体育项目', FXKC: '辅修课程', ALLKC: '全校课程查询',
  BYKC: '本研课程', ZYNKC: '专业内课程',
};
const FILTER_LABELS = {
  courseNature: '课程性质', courseCategory: '课程类别',
  generalElectiveCategory: '通识选修课类别', campus: '校区',
  department: '开课单位', startSection: '开始节次', endSection: '结束节次',
};

const classScheduleText = course => (course.schedules || []).map(item => (
  `${item.week_text || '周次待定'} · 周${WEEKDAYS[(item.weekday || 1) - 1] || '-'} · ${item.start_section || '-'}-${item.end_section || '-'}节`
)).join('；') || '时间待定';

const planItem = (group, course, scope, planGroup, priority) => ({
  plan_group_id: planGroup.group_id,
  plan_group_name: planGroup.name,
  plan_group_target_count: planGroup.target_count,
  group_id: group.group_id,
  course_code: course.course_code,
  course_name: course.course_name,
  class_id: course.class_id,
  class_number: course.class_number,
  teaching_class_type: course.teaching_class_type || scope,
  teacher: course.teacher,
  location: course.location,
  campus: course.campus,
  course_nature: course.course_nature || group.course_nature,
  course_category: course.course_category || group.course_category,
  capacity: course.capacity,
  selected_count: course.selected_count,
  first_choice_count: course.first_choice_count,
  weight_participant_count: course.weight_participant_count,
  utility: 5,
  priority,
  schedules: course.schedules || [],
});

const scheduleOverlayMeta = layer => ({
  preview: { label: '正在预览', tag: '正在预览', color: '#2563eb' },
  candidate: { label: '待选方案', tag: '待选方案', color: '#2563eb' },
  pending: { label: '已投权待结果', tag: '已投权', color: '#2563eb' },
  selected: { label: '已选课程', tag: '已选', color: '#16a34a' },
}[layer] || { label: '待选课程', tag: '待选', color: '#2563eb' });

const scheduleOverlayForCourse = (item, layer, idPrefix) => (item.schedules || []).map((meeting, index) => ({
  ...meeting,
  id: `${idPrefix}-${item.class_id}-${index}`,
  meeting_id: `${idPrefix}-${item.class_id}-${index}`,
  source_id: item.class_id,
  course_name: item.course_name,
  course_code: item.course_code,
  teaching_class_id: item.class_id,
  weekday: Number(meeting.weekday || 0),
  start_section: Number(meeting.start_section || 0),
  end_section: Number(meeting.end_section || meeting.start_section || 0),
  weeks: Array.isArray(meeting.weeks) ? meeting.weeks.map(Number).filter(Number.isFinite) : [],
  recurrence_unknown: Boolean(meeting.recurrence_unknown || !meeting.weeks?.length),
  location: meeting.location || item.location || '',
  campus: meeting.campus_name || item.campus_name || meeting.campus || item.campus || '',
  teachers: item.teacher ? [item.teacher] : [],
  classes: item.class_number ? [item.class_number] : [],
  course_type: scheduleOverlayMeta(layer).label,
  tags: [scheduleOverlayMeta(layer).tag],
  title_details: [meeting.raw_text, item.official_schedule].filter(Boolean),
  color: scheduleOverlayMeta(layer).color,
  layer,
}));

const mergedFilterOptions = (loaded = [], values = []) => {
  const result = new Map((loaded || []).map(item => [String(item.value), item]));
  values.filter(Boolean).forEach(value => {
    const key = String(value);
    if (!result.has(key)) result.set(key, { value: key, label: key });
  });
  return [...result.values()].sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'));
};

const mergedCampusFilterOptions = (loaded = [], candidates = []) => {
  const result = new Map((loaded || []).map(item => [String(item.value), item]));
  const knownLabels = new Set([...result.values()].map(item => String(item.label)));
  (candidates || []).forEach(candidate => {
    const value = String(candidate?.value || '').trim();
    const rawLabel = String(candidate?.label || value).trim();
    const label = /^[A-Za-z0-9_-]+$/.test(rawLabel) ? '其他校区' : rawLabel;
    if (!value || knownLabels.has(label)) return;
    if (!result.has(value)) result.set(value, { value, label });
    knownLabels.add(label);
  });
  return [...result.values()].sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'));
};

const courseScopeLabel = value => COURSE_SCOPE_LABELS[value]
  || (/^[A-Z0-9_]+$/.test(String(value || '')) ? '其他课程' : value)
  || '其他课程';
const batchScopeOptions = batch => {
  const options = [
    { code: 'ALL', name: '所有课程' },
    { code: 'ROUND', name: '本轮课程' },
    ...(batch?.menus || []),
  ];
  return [...new Map(options.map(item => {
    const code = String(item?.code || item?.teachingClassType || '').trim();
    const officialName = String(item?.name || item?.displayName || '').trim();
    return [code, { code, name: officialName || courseScopeLabel(code) }];
  }).filter(([code]) => code)).values()];
};
const displayCampusName = value => (/^[A-Za-z0-9_-]+$/.test(String(value || '')) ? '其他校区' : value);

const EMPTY_CATALOG_FILTERS = Object.freeze({
  courseNature: '', courseCategory: '', generalElectiveCategory: '',
  campus: '', department: '', startSection: '', endSection: '',
});

const cleanCatalogFilters = value => Object.fromEntries(
  Object.keys(EMPTY_CATALOG_FILTERS).map(key => [key, String(value?.[key] || '').trim()]),
);

const mergedCategoryFilterOptions = (loaded = [], values = []) => {
  const normalize = value => ['通识选修类', '通识选修课', '通识选修课程']
    .includes(String(value || '').trim()) ? '通识选修' : String(value || '').trim();
  const normalizedLoaded = (loaded || []).map(item => {
    const value = normalize(typeof item === 'string' ? item : item.value);
    return { value, label: value };
  }).filter(item => item.value);
  return mergedFilterOptions(normalizedLoaded, values.map(normalize));
};

const mergeEligibilityResults = (groups, results = []) => {
  const resultMap = new Map(results.map(item => [item.class_id, item]));
  return groups.map(group => {
    const classes = (group.classes || []).map(course => {
      const result = resultMap.get(course.class_id);
      return result ? {
        ...course,
        eligibility_status: result.status,
        eligibility_reason: result.reason || '',
      } : course;
    });
    return {
      ...group,
      classes,
      selectable_count: classes.filter(course => (
        course.eligibility_status === 'selectable' && !course.full && !course.restricted
      )).length,
      eligibility_pending_count: classes.filter(course => course.eligibility_status === 'unknown').length,
    };
  });
};

const selectionScheduleFromRecords = courses => ({
  source: 'selected_records_fallback',
  source_label: '根据官方已选记录生成',
  courses,
  meetings: courses.flatMap(course => (course.schedules || []).map((meeting, index) => ({
    ...meeting,
    candidate_id: `${course.class_id || course.course_code}:${index}`,
    course_code: course.course_code || '',
    course_name: course.course_name || '',
    teaching_class_id: course.class_id || '',
  }))),
});

const selectionRecordsFromResponse = result => {
  const confirmed = (result?.selected || []).map(item => ({
    ...item, selection_record_type: 'selected',
  }));
  const volunteered = (result?.volunteered || []).map(item => ({
    ...item, selection_record_type: 'volunteered',
  }));
  const merged = [...new Map([...confirmed, ...volunteered].map(item => [
    item.class_id || `${item.course_code}:${item.course_name}`, item,
  ])).values()];
  return { confirmed, volunteered, merged };
};

const CourseSelectionWorkspacePage = () => {
  const { batchCode } = useParams();
  const navigate = useNavigate();
  // The selection workspace only consumes the report already maintained by the
  // academic-report resource.  Do not let entering JWXK start another remote
  // refresh, and let this small local read win the browser connection race
  // before the slower JWXK status/catalog requests begin.
  const academicReportResource = useCachedResource('academic-report', { autoRefresh: false });
  const academicReportCacheSettled = Boolean(
    academicReportResource.data || !academicReportResource.loading,
  );
  const requestGeneration = useRef(0);
  const workspaceGeneration = useRef(0);
  const planSaveQueue = useRef(Promise.resolve());
  const filterOptionsPromiseRef = useRef(null);
  const catalogRef = useRef(null);
  const scheduleRef = useRef(null);
  const courseCardRefs = useRef(new Map());
  const courseClassRefs = useRef(new Map());
  const focusInProgressRef = useRef(false);
  const capacityRefreshInFlightRef = useRef(false);
  const selectedMarketRefreshInFlightRef = useRef(false);
  const tasksRefreshInFlightRef = useRef(false);
  const taskAttentionTimerRef = useRef(null);
  const selectedRef = useRef([]);
  const catalogDisplayLayoutRef = useRef({ signature: '', layout: [] });
  const [status, setStatus] = useState(null);
  const [localBatch, setLocalBatch] = useState(null);
  const [savedTermCode, setSavedTermCode] = useState('');
  const [view, setView] = useState('catalog');
  const [scope, setScope] = useState('ALL');
  const [scopeOptions, setScopeOptions] = useState([]);
  const [availability, setAvailability] = useState('all');
  const [weekday, setWeekday] = useState('all');
  const [timeSlot, setTimeSlot] = useState(null);
  const [catalogFilters, setCatalogFilters] = useState({ ...EMPTY_CATALOG_FILTERS });
  const [planGapFilters, setPlanGapFilters] = useState({ ...EMPTY_CATALOG_FILTERS });
  const [filterDraft, setFilterDraft] = useState(catalogFilters);
  const [filterOptions, setFilterOptions] = useState(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterLoading, setFilterLoading] = useState(false);
  const [keywordDraft, setKeywordDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  const [groups, setGroups] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [capacityRefreshing, setCapacityRefreshing] = useState(false);
  const [capacityUpdatedAt, setCapacityUpdatedAt] = useState(null);
  const [actionLoading, setActionLoading] = useState('');
  const [pendingVerificationClassIds, setPendingVerificationClassIds] = useState([]);
  const [eligibilityLoading, setEligibilityLoading] = useState([]);
  const [plan, setPlan] = useState([]);
  const [planGroupConfigs, setPlanGroupConfigs] = useState([]);
  const [taskGroupIds, setTaskGroupIds] = useState([]);
  const [conflicts, setConflicts] = useState({});
  const [selected, setSelected] = useState([]);
  const [confirmedSelected, setConfirmedSelected] = useState([]);
  const [schedule, setSchedule] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [tasksRefreshing, setTasksRefreshing] = useState(false);
  const [taskActionLoading, setTaskActionLoading] = useState('');
  const [attentionTaskId, setAttentionTaskId] = useState('');
  const [expandedGroupId, setExpandedGroupId] = useState('');
  const [catalogPreviewClasses, setCatalogPreviewClasses] = useState([]);
  const [personalCourses, setPersonalCourses] = useState([]);
  const [personalScheduleReady, setPersonalScheduleReady] = useState(false);
  const [weightPlan, setWeightPlan] = useState(null);
  const [weightSetupOpen, setWeightSetupOpen] = useState(false);
  const [weightBuilding, setWeightBuilding] = useState(false);
  const [gradeSizeDraft, setGradeSizeDraft] = useState(null);
  const [focusedGroupId, setFocusedGroupId] = useState('');
  const [selectedUpdatedAt, setSelectedUpdatedAt] = useState(null);
  const [selectedRefreshing, setSelectedRefreshing] = useState(false);
  const [planAssignment, setPlanAssignment] = useState(null);
  const [assignmentGroupId, setAssignmentGroupId] = useState('');
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupTargetCount, setNewGroupTargetCount] = useState(1);
  const [groupEditor, setGroupEditor] = useState(null);
  const [vacancySwapTarget, setVacancySwapTarget] = useState(null);
  const [vacancyDropIds, setVacancyDropIds] = useState([]);
  const [activePlanGapId, setActivePlanGapId] = useState('');
  const [activePlanGapLabel, setActivePlanGapLabel] = useState('');
  const [activePlanGapCategoryMatched, setActivePlanGapCategoryMatched] = useState(true);
  const [outlineCourse, setOutlineCourse] = useState(null);
  const remoteAvailability = catalogAvailabilityRequestMode(availability);
  const effectiveCatalogFilters = useMemo(() => mergeCatalogFilterLayers(
    catalogFilters, planGapFilters, Object.keys(EMPTY_CATALOG_FILTERS),
  ), [catalogFilters, planGapFilters]);

  const remoteBatch = useMemo(
    () => (status?.batches || []).find(item => item.code === batchCode),
    [status, batchCode],
  );
  const batch = remoteBatch || localBatch;
  const academicPlanSelected = useMemo(() => academicPlanSelectionRecords(
    selected, batch?.selection_type_code,
  ), [batch?.selection_type_code, selected]);
  const academicPlanProjection = useMemo(() => overlayExternalSelectedCourses(
    academicReportResource.data?.categories || [],
    academicPlanSelected,
  ), [academicPlanSelected, academicReportResource.data]);
  const academicPlanGaps = useMemo(() => collectAcademicPlanDeficits(
    academicPlanProjection.categories,
  ), [academicPlanProjection.categories]);
  const visibleAcademicPlanGaps = useMemo(
    () => filterAcademicPlanGapsForBatch(academicPlanGaps, batch),
    [academicPlanGaps, batch],
  );
  const termCode = batch?.term_code || savedTermCode || '';
  const planGroups = useMemo(() => {
    const configured = Object.fromEntries(planGroupConfigs.map(group => [group.group_id, { ...group, id: group.group_id, items: [] }]));
    const grouped = plan.reduce((result, item) => {
    const key = item.plan_group_id || item.course_code || item.group_id;
    if (!result[key]) result[key] = {
      id: key,
      group_id: key,
      name: item.plan_group_name || item.course_name,
      target_count: item.plan_group_target_count || 1,
      items: [],
    };
    result[key].items.push(item);
    return result;
    }, configured);
    return Object.values(grouped).map(group => ({
      ...group,
      target_count: Math.max(1, Number(group.target_count || 1)),
      items: [...group.items].sort((a, b) => (a.priority || 99) - (b.priority || 99)),
    }));
  }, [plan, planGroupConfigs]);
  const selectedByCourseCode = useMemo(() => new Map(
    selected.filter(item => item.course_code).map(item => [String(item.course_code).toUpperCase(), item]),
  ), [selected]);
  const selectedByClassId = useMemo(() => new Map(
    selected.filter(item => item.class_id).map(item => [String(item.class_id), item]),
  ), [selected]);
  const volunteeredCourseCodes = useMemo(() => new Set(
    selected
      .filter(item => (
        item.selection_record_type === 'volunteered'
        && isCurrentBatchSelectionRecord(item, batch?.selection_type_code)
      ))
      .map(item => String(item.course_code || '').trim().toUpperCase())
      .filter(Boolean),
  ), [batch?.selection_type_code, selected]);
  const volunteeredClassIds = useMemo(() => new Set(
    selected
      .filter(item => (
        item.selection_record_type === 'volunteered'
        && isCurrentBatchSelectionRecord(item, batch?.selection_type_code)
      ))
      .map(item => String(item.class_id || '').trim())
      .filter(Boolean),
  ), [batch?.selection_type_code, selected]);
  const orderedSelected = useMemo(() => [...selected].sort((left, right) => (
    Number(isCurrentBatchSelectionRecord(right, batch?.selection_type_code))
    - Number(isCurrentBatchSelectionRecord(left, batch?.selection_type_code))
  )), [selected, batch?.selection_type_code]);
  const catalogDisplaySignature = useMemo(() => JSON.stringify({
    batchCode, page, keyword, scope, availability, weekday, timeSlot,
    filters: effectiveCatalogFilters,
  }), [availability, batchCode, effectiveCatalogFilters, keyword, page, scope, timeSlot, weekday]);
  const visibleGroups = useMemo(() => {
    const currentlyMatching = catalogGroupsForDisplay(groups, { availability, weekday });
    const previousLayout = catalogDisplayLayoutRef.current;
    const layout = previousLayout.signature === catalogDisplaySignature
      ? extendCatalogDisplayLayout(previousLayout.layout, currentlyMatching)
      : createCatalogDisplayLayout(currentlyMatching);
    catalogDisplayLayoutRef.current = { signature: catalogDisplaySignature, layout };
    return applyCatalogDisplayLayout(groups, layout);
  }, [availability, catalogDisplaySignature, groups, weekday]);

  const fetchEligibility = async classIds => {
    const ids = [...new Set(classIds.filter(Boolean))];
    if (!ids.length) return [];
    setEligibilityLoading(previous => [...new Set([...previous, ...ids])]);
    try {
      const results = [];
      for (let index = 0; index < ids.length; index += 50) {
        const response = await checkJwxkCatalogEligibility(batchCode, ids.slice(index, index + 50));
        results.push(...(response.results || []));
      }
      return results;
    } finally {
      setEligibilityLoading(previous => previous.filter(value => !ids.includes(value)));
    }
  };

  const verifyEligibility = async classIds => {
    try {
      const results = await fetchEligibility(classIds);
      setGroups(previous => mergeEligibilityResults(previous, results));
      return results;
    } catch (error) {
      message.error(error.message || '核验教学班可选性失败');
      return [];
    }
  };

  const loadCatalog = async (
    targetPage = 1,
    targetKeyword = keyword,
    targetScope = scope,
    targetTimeSlot = timeSlot,
    targetFilters = effectiveCatalogFilters,
    targetWeekday = weekday,
    options = {},
  ) => {
    const silent = Boolean(options.silent);
    const skipLocal = Boolean(options.skipLocal);
    if (silent && capacityRefreshInFlightRef.current) return;
    const generation = ++requestGeneration.current;
    if (silent) {
      capacityRefreshInFlightRef.current = true;
      setCapacityRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const safeFilters = cleanCatalogFilters(targetFilters);
      const safeScope = /^[A-Z0-9_]+$/.test(String(targetScope || '')) ? String(targetScope) : 'ALL';
      const safeCampus = String(safeFilters.campus || '').trim();
      const safeTimeSlot = targetTimeSlot
        && Number(targetTimeSlot.weekday) >= 1 && Number(targetTimeSlot.weekday) <= 7
        && Number(targetTimeSlot.section) >= 1 && Number(targetTimeSlot.section) <= 30
        ? { weekday: Number(targetTimeSlot.weekday), section: Number(targetTimeSlot.section) }
        : null;
      const remoteFilters = {
        ...(targetWeekday !== 'all' ? { SKXQ: targetWeekday } : {}),
        ...catalogAvailabilityRemoteFilters(remoteAvailability),
        ...(safeFilters.courseNature ? { KCXZ: safeFilters.courseNature } : {}),
        ...(safeFilters.courseCategory ? { KCLB: safeFilters.courseCategory } : {}),
        ...(safeFilters.generalElectiveCategory ? { XGXKLB: safeFilters.generalElectiveCategory } : {}),
        ...(safeFilters.department ? { KKDW: safeFilters.department } : {}),
        ...(safeFilters.startSection ? { KSJC: safeFilters.startSection } : {}),
        ...(safeFilters.endSection ? { JSJC: safeFilters.endSection } : {}),
      };
      const payload = {
        batch_code: batchCode, page_number: targetPage, page_size: 20,
        keyword: String(targetKeyword || '').trim(), scope: safeScope, campus: safeCampus,
        order_by: '', filters: remoteFilters, time_slot: safeTimeSlot,
      };
      const applyResult = result => {
        const nextGroups = result.groups || [];
        setGroups(previous => silent
          ? mergeCatalogRefreshPreservingOrder(previous, nextGroups)
          : nextGroups);
        setTotal(result.total || 0); setPage(targetPage);
        setScope(result.scope || targetScope);
        if (result.scope_options?.length) setScopeOptions(result.scope_options);
        return nextGroups;
      };
      if (!silent && !skipLocal) {
        try {
          const localResult = await searchJwxkCatalog({ ...payload, local_only: true });
          if (generation !== requestGeneration.current) return [];
          if (localResult.cache_hit) {
            const localGroups = applyResult(localResult);
            setLoading(false);
            window.setTimeout(() => {
              if (generation === requestGeneration.current) {
                void loadCatalog(
                  targetPage, targetKeyword, targetScope, targetTimeSlot,
                  targetFilters, targetWeekday, { silent: true, skipLocal: true },
                );
              }
            }, 0);
            return localGroups;
          }
        } catch (_error) {
          // 本地目录不可用时继续读取学校数据；首屏行为与首次使用保持兼容。
        }
      }
      const result = await searchJwxkCatalog(
        { ...payload, local_only: false },
        silent ? { skipAuthRedirect: true } : {},
      );
      if (generation !== requestGeneration.current) return;
      const nextGroups = applyResult(result);
      setCapacityUpdatedAt(new Date());
      if (!silent) setLoading(false);
      if (silent) return;
      const classIds = nextGroups.flatMap(group => (
        group.classes || []
      ).map(course => course.class_id)).filter(Boolean);
      try {
        const eligibility = await fetchEligibility(classIds);
        if (generation !== requestGeneration.current) return;
        setGroups(previous => mergeEligibilityResults(previous, eligibility));
      } catch (error) {
        if (generation === requestGeneration.current) {
          message.error(error.message || '核验本页教学班可选性失败');
        }
      }
      return nextGroups;
    } catch (error) {
      if (!silent && generation === requestGeneration.current) {
        message.error(error.message || '读取课程目录失败');
      }
      return [];
    } finally {
      if (silent) {
        capacityRefreshInFlightRef.current = false;
        setCapacityRefreshing(false);
      } else if (generation === requestGeneration.current) {
        setLoading(false);
      }
    }
  };

  const commitSelectedRecords = records => {
    selectedRef.current = records;
    setSelected(records);
    setSchedule(selectionScheduleFromRecords(records));
  };

  const removeSelectedCourseLocally = course => {
    commitSelectedRecords(removeSelectionRecord(selectedRef.current, course));
    setConfirmedSelected(previous => removeSelectionRecord(previous, course));
    setGroups(previous => patchCatalogSelection(previous, course, { selected: false }));
  };

  const upsertSelectedCourseLocally = (record, { confirmed = false } = {}) => {
    commitSelectedRecords(upsertSelectionRecord(selectedRef.current, record));
    setConfirmedSelected(previous => confirmed
      ? upsertSelectionRecord(previous, record)
      : removeSelectionRecord(previous, record));
    setGroups(previous => patchCatalogSelection(previous, record, {
      selected: true,
      devotedWeight: record.devoted_weight ?? null,
    }));
  };

  const optimisticSelectionRecord = (group, course, weight) => ({
    ...course,
    course_name: group.course_name || course.course_name,
    course_code: group.course_code || course.course_code,
    devoted_weight: weight,
    selected: batch?.selection_type_code !== '04',
    course_already_selected: true,
    selection_record_type: batch?.selection_type_code === '04' ? 'volunteered' : 'selected',
    selection_source: batch?.selection_type_code === '04' ? 'fakcyx' : 'yxkcyx',
  });

  const loadSelected = async ({ silent = false, propagateError = false, includeMarket = false } = {}) => {
    if (includeMarket && selectedMarketRefreshInFlightRef.current) return null;
    if (includeMarket) selectedMarketRefreshInFlightRef.current = true;
    if (silent) setSelectedRefreshing(true);
    else setLoading(true);
    try {
      const result = await getJwxkSelected(
        batchCode,
        { ...(silent ? { skipAuthRedirect: true } : {}), includeMarket },
      );
      const { confirmed, merged } = selectionRecordsFromResponse(result);
      setConfirmedSelected(confirmed);
      commitSelectedRecords(merged);
      setSelectedUpdatedAt(new Date());
      return merged;
    } catch (error) {
      if (!silent) message.error(error.message || '读取已选结果失败');
      if (propagateError) throw error;
      return null;
    } finally {
      if (includeMarket) selectedMarketRefreshInFlightRef.current = false;
      if (silent) setSelectedRefreshing(false);
      else setLoading(false);
    }
  };

  const verifySubmittedSelection = async (group, course) => {
    const generation = workspaceGeneration.current;
    const actionName = batch?.selection_type_code === '04' ? '投权' : '选课';
    setPendingVerificationClassIds(previous => [...new Set([...previous, course.class_id])]);
    try {
      let snapshot = null;
      let matched = null;
      let lastError = null;
      for (let attempt = 0; attempt < 2; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, attempt === 0 ? 250 : 1200));
        try {
          const result = await getJwxkSelected(batchCode, {
            includeMarket: false, skipAuthRedirect: true,
          });
          snapshot = selectionRecordsFromResponse(result);
          lastError = null;
          if (generation !== workspaceGeneration.current) return;
          matched = findMatchingSelectionRecord(snapshot.merged, {
            ...course,
            course_name: group.course_name || course.course_name,
            course_code: group.course_code || course.course_code,
          });
          if (matched) break;
        } catch (error) {
          lastError = error;
        }
      }
      if (generation !== workspaceGeneration.current) return;
      if (matched) {
        upsertSelectedCourseLocally(matched, {
          confirmed: matched.selection_record_type === 'selected',
        });
      } else if (lastError) {
        message.warning({
          content: `${actionName}已收到官方成功响应，但后台暂时无法读取结果；前台不会因此阻塞`,
          duration: 7,
        });
      } else {
        removeSelectedCourseLocally(course);
        message.warning({
          content: `后台核验未在官方结果中找到“${group.course_name || course.course_name}”，已纠正本地显示`,
          duration: 7,
        });
      }
    } finally {
      if (generation === workspaceGeneration.current) {
        setPendingVerificationClassIds(previous => previous.filter(value => value !== course.class_id));
      }
    }
  };

  const verifyDeselectedCourse = async (course, { optimisticRemoved = false } = {}) => {
    const generation = workspaceGeneration.current;
    setPendingVerificationClassIds(previous => [...new Set([...previous, course.class_id])]);
    try {
      let matched = null;
      let lastError = null;
      for (let attempt = 0; attempt < 2; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, attempt === 0 ? 250 : 1200));
        try {
          const result = await getJwxkSelected(batchCode, {
            includeMarket: false, skipAuthRedirect: true,
          });
          const snapshot = selectionRecordsFromResponse(result);
          if (generation !== workspaceGeneration.current) return;
          matched = findMatchingSelectionRecord(snapshot.merged, course);
          lastError = null;
          if (!matched) {
            if (!optimisticRemoved) removeSelectedCourseLocally(course);
            return;
          }
        } catch (error) {
          lastError = error;
        }
      }
      if (generation !== workspaceGeneration.current) return;
      if (matched) {
        if (optimisticRemoved) {
          upsertSelectedCourseLocally(matched, {
            confirmed: matched.selection_record_type === 'selected',
          });
          message.warning(`后台核验发现“${course.course_name}”仍在官方结果中，已恢复本地显示`);
        } else {
          message.info(`“${course.course_name}”仍在官方处理中，稍后会继续按实时结果更新`);
        }
      } else if (lastError) {
        message.warning('退选已收到官方成功响应，但后台结果暂时无法读取；前台不会因此阻塞');
      }
    } finally {
      if (generation === workspaceGeneration.current) {
        setPendingVerificationClassIds(previous => previous.filter(value => value !== course.class_id));
      }
    }
  };

  const loadTasks = async ({ silent = false } = {}) => {
    if (silent && tasksRefreshInFlightRef.current) return;
    const generation = workspaceGeneration.current;
    if (silent) {
      tasksRefreshInFlightRef.current = true;
      setTasksRefreshing(true);
    }
    try {
      const result = await listJwxkAutomationTasks(batchCode);
      if (generation === workspaceGeneration.current) setTasks(result.tasks || []);
    }
    catch (error) {
      if (!silent) message.error(error.message || '读取自动任务失败');
    } finally {
      if (silent) {
        tasksRefreshInFlightRef.current = false;
        if (generation === workspaceGeneration.current) setTasksRefreshing(false);
      }
    }
  };

  const runTaskAction = async (task, action) => {
    const actionKey = `${task.task_id}:${action}`;
    if (action === 'start' && attentionTaskId === task.task_id) {
      if (taskAttentionTimerRef.current) window.clearTimeout(taskAttentionTimerRef.current);
      taskAttentionTimerRef.current = null;
      setAttentionTaskId('');
    }
    setTaskActionLoading(actionKey);
    try {
      const updated = await actionJwxkAutomationTask(task.task_id, action);
      setTasks(previous => action === 'cancel'
        ? previous.filter(item => item.task_id !== task.task_id)
        : previous.map(item => item.task_id === task.task_id ? { ...item, ...updated } : item));
      if (action === 'start') message.success('任务已启动，页面将实时显示后台执行进度');
      if (action === 'pause') message.success('任务已暂停');
      if (action === 'check_now') message.success('已请求立即检查；如果当前一轮仍在执行，会在结束后立即再检查并按策略行动');
      await loadTasks({ silent: true });
    } catch (error) {
      message.error(error.message || '更新自动任务失败');
    } finally {
      setTaskActionLoading('');
    }
  };

  useEffect(() => {
    if (!academicReportCacheSettled) return undefined;
    const generation = ++workspaceGeneration.current;
    ++requestGeneration.current;
    setStatus(null);
    setLocalBatch(null);
    setScopeOptions([]);
    setSavedTermCode('');
    setPlan([]);
    setPlanGroupConfigs([]);
    setTaskGroupIds([]);
    setSelected([]);
    selectedRef.current = [];
    setConfirmedSelected([]);
    setSchedule(null);
    setTasks([]);
    setTasksRefreshing(false);
    setTaskActionLoading('');
    setAttentionTaskId('');
    if (taskAttentionTimerRef.current) window.clearTimeout(taskAttentionTimerRef.current);
    taskAttentionTimerRef.current = null;
    tasksRefreshInFlightRef.current = false;
    setGroups([]);
    setCapacityRefreshing(false);
    setCapacityUpdatedAt(null);
    setPendingVerificationClassIds([]);
    capacityRefreshInFlightRef.current = false;
    selectedMarketRefreshInFlightRef.current = false;
    setFilterOptions(null);
    setFilterLoading(false);
    filterOptionsPromiseRef.current = null;
    setTotal(0);
    setPage(1);
    setExpandedGroupId('');
    catalogDisplayLayoutRef.current = { signature: '', layout: [] };
    setCatalogPreviewClasses([]);
    setConflicts({});
    setPersonalCourses([]);
    setPersonalScheduleReady(false);
    setPlanAssignment(null);
    setGroupEditor(null);
    setVacancySwapTarget(null);
    setVacancyDropIds([]);
    setActivePlanGapId('');
    setActivePlanGapLabel('');
    setActivePlanGapCategoryMatched(true);
    setOutlineCourse(null);
    setWeightPlan(null);
    setWeightSetupOpen(false);
    setGradeSizeDraft(null);
    setFocusedGroupId('');
    setSelectedUpdatedAt(null);
    setSelectedRefreshing(false);
    planSaveQueue.current = Promise.resolve();
    setScope('ALL');
    readJwxkPlan(batchCode).then(saved => {
      if (generation !== workspaceGeneration.current || saved.batch_code !== batchCode) return;
      const savedItems = (saved.items || []).map(item => {
        const planGroupId = item.plan_group_id || item.group_id || item.course_code || item.class_id;
        return planGroupId ? { ...item, plan_group_id: planGroupId } : item;
      });
      const savedGroups = saved.groups?.length ? saved.groups : Object.values(savedItems.reduce((result, item) => {
        const groupId = item.plan_group_id || item.course_code || item.class_id;
        if (groupId && !result[groupId]) result[groupId] = {
          group_id: groupId,
          name: item.plan_group_name || item.course_name || '方案组',
          target_count: item.plan_group_target_count || 1,
        };
        return result;
      }, {}));
      setPlan(savedItems); setPlanGroupConfigs(savedGroups);
      setTaskGroupIds(savedGroups.map(group => group.group_id));
      setSavedTermCode(saved.term_code || '');
      setLocalBatch(saved.batch || null);
      const savedScopeOptions = batchScopeOptions(saved.batch);
      if (savedScopeOptions.length > 2) setScopeOptions(savedScopeOptions);
    }).catch(error => message.error(error.message || '读取本地选课数据失败'));
    getJwxkStatus().then(nextStatus => {
      if (generation !== workspaceGeneration.current) return;
      setStatus(nextStatus);
      const nextBatch = (nextStatus.batches || []).find(item => item.code === batchCode);
      const nextScopeOptions = batchScopeOptions(nextBatch);
      if (nextScopeOptions.length > 2) setScopeOptions(nextScopeOptions);
    }).catch(error => message.warning(error.message || '暂时无法刷新选课轮次状态，已继续显示本地数据'));
    getJwxkSelected(batchCode, { includeMarket: false }).then(result => {
      if (generation !== workspaceGeneration.current) return;
      const { confirmed, merged } = selectionRecordsFromResponse(result);
      setConfirmedSelected(confirmed);
      commitSelectedRecords(merged);
    }).catch(() => {
      // 已选结果暂不可用时仍允许浏览目录；后端提交前仍会执行同课程代码防重。
    });
    return () => {
      if (taskAttentionTimerRef.current) window.clearTimeout(taskAttentionTimerRef.current);
      taskAttentionTimerRef.current = null;
      if (workspaceGeneration.current === generation) ++workspaceGeneration.current;
    };
  }, [academicReportCacheSettled, batchCode]);

  useEffect(() => {
    if (!academicReportCacheSettled) return undefined;
    if (focusInProgressRef.current) return undefined;
    const timer = window.setTimeout(() => {
      loadCatalog(1, keyword, scope, timeSlot, effectiveCatalogFilters, weekday);
    }, 80);
    return () => window.clearTimeout(timer);
  }, [academicReportCacheSettled, batchCode, effectiveCatalogFilters, keyword, remoteAvailability, scope, timeSlot, weekday]);

  useEffect(() => {
    if (!batch || batch.state !== 'active' || view !== 'catalog' || loading) return undefined;
    const refreshCapacity = () => {
      if (document.visibilityState !== 'visible') return;
      loadCatalog(
        page, keyword, scope, timeSlot, effectiveCatalogFilters, weekday,
        { silent: true },
      );
    };
    const timer = window.setInterval(refreshCapacity, CATALOG_CAPACITY_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [batch, effectiveCatalogFilters, keyword, loading, page, remoteAvailability, scope, timeSlot, view, weekday]);

  const ensureCatalogFilterOptions = async () => {
    if (filterOptions) return filterOptions;
    if (filterOptionsPromiseRef.current) return filterOptionsPromiseRef.current;
    const generation = workspaceGeneration.current;
    const targetBatchCode = batchCode;
    setFilterLoading(true);
    const operation = (async () => {
      const result = await getJwxkCatalogFilterOptions(targetBatchCode);
      if (generation !== workspaceGeneration.current || targetBatchCode !== batchCode) return result;
      setFilterOptions(result);
      if (result.scopes?.length) setScopeOptions(result.scopes);
      return result;
    })();
    filterOptionsPromiseRef.current = operation;
    try { return await operation; }
    finally {
      if (filterOptionsPromiseRef.current === operation) {
        filterOptionsPromiseRef.current = null;
        setFilterLoading(false);
      }
    }
  };

  const openCatalogFilters = async () => {
    setFilterDraft(catalogFilters);
    setFilterOpen(true);
    try { await ensureCatalogFilterOptions(); }
    catch (error) { message.error(error.message || '读取课程筛选项失败'); }
  };

  const effectiveFilterOptions = useMemo(() => ({
    course_natures: mergedFilterOptions(filterOptions?.course_natures, groups.map(group => group.course_nature)),
    course_categories: mergedCategoryFilterOptions(
      filterOptions?.course_categories,
      groups.flatMap(group => [
        group.normalized_course_category,
        ...(group.course_categories || []),
        group.course_category,
      ]),
    ),
    general_elective_categories: mergedFilterOptions(
      filterOptions?.general_elective_categories,
      groups.map(group => group.general_elective_category),
    ),
    campuses: mergedCampusFilterOptions([
      ...(filterOptions?.campuses || []),
      ...(status?.current_campus ? [{ value: status.current_campus, label: status.current_campus_name || status.current_campus }] : []),
    ], groups.flatMap(group => (group.classes || []).flatMap(course => [
      { value: course.campus, label: course.campus_name || course.campus },
      ...(course.schedules || []).map(item => ({ value: item.campus, label: item.campus_name || item.campus })),
    ]))),
    departments: mergedFilterOptions(filterOptions?.departments, groups.map(group => group.department)),
    sections: filterOptions?.sections || Array.from({ length: 30 }, (_, index) => ({ value: String(index + 1), label: `第${index + 1}节` })),
  }), [filterOptions, groups, status]);
  const campusOptionMap = useMemo(() => new Map(
    effectiveFilterOptions.campuses.flatMap(item => [
      [String(item.value), item.label], [String(item.label), item.label],
    ]),
  ), [effectiveFilterOptions.campuses]);
  const campusLabel = value => campusOptionMap.get(String(value || '')) || displayCampusName(String(value || ''));
  const filterValueLabel = (key, value) => {
    if (key === 'campus') return campusLabel(value);
    if (key === 'startSection' || key === 'endSection') return `第${value}节`;
    return value;
  };

  useEffect(() => {
    if (view === 'selected') {
      loadSelected({ silent: selected.length > 0, includeMarket: false }).then(result => {
        if (result) loadSelected({ silent: true, includeMarket: true });
      });
    }
    if (view === 'tasks') loadTasks();
  }, [view]);

  useEffect(() => {
    if (view !== 'tasks') return undefined;
    const refresh = () => {
      if (document.visibilityState === 'visible') loadTasks({ silent: true });
    };
    const timer = window.setInterval(refresh, TASK_STATUS_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [batchCode, view]);

  useEffect(() => {
    if (!batch || batch.state !== 'active' || view !== 'selected') return undefined;
    const refresh = () => {
      if (document.visibilityState === 'visible') {
        loadSelected({ silent: true, includeMarket: true });
      }
    };
    const timer = window.setInterval(refresh, CATALOG_CAPACITY_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [batch, view]);

  const savePlan = async (next, nextGroups = planGroupConfigs) => {
    const targetBatchCode = batchCode;
    const targetTermCode = termCode || 'unknown';
    setPlan(next); setPlanGroupConfigs(nextGroups);
    const operation = planSaveQueue.current.then(() => saveJwxkPlan({
      batch_code: targetBatchCode,
      term_code: targetTermCode,
      groups: nextGroups,
      items: next,
    }));
    planSaveQueue.current = operation.catch(() => undefined);
    try { await operation; }
    catch (error) { message.error(error.message || '保存方案失败'); }
  };

  const openPlanAssignment = (group, course) => {
    const duplicate = selectedByCourseCode.get(String(course.course_code || '').toUpperCase());
    if (duplicate) {
      message.info(`“${duplicate.course_name || course.course_name}”已经选中；仍可加入方案组，但不会允许再次手动提交`);
    }
    if (plan.some(item => item.class_id === course.class_id)) {
      message.info('这个教学班已经在方案中'); return;
    }
    setPlanAssignment({ group, course });
    setAssignmentGroupId(planGroupConfigs[0]?.group_id || '__new__');
    setNewGroupName('');
    setNewGroupTargetCount(1);
  };

  const confirmPlanAssignment = async () => {
    if (!planAssignment) return;
    let nextGroups = planGroupConfigs;
    let targetGroup = planGroupConfigs.find(group => group.group_id === assignmentGroupId);
    if (assignmentGroupId === '__new__') {
      if (!newGroupName.trim()) throw new Error('请填写方案组名称');
      targetGroup = {
        group_id: `group_${Date.now().toString(36)}`,
        name: newGroupName.trim(),
        target_count: Number(newGroupTargetCount || 1),
      };
      nextGroups = [...planGroupConfigs, targetGroup];
    }
    if (!targetGroup) throw new Error('请选择方案组');
    const groupItems = plan.filter(item => item.plan_group_id === targetGroup.group_id);
    const next = [...plan, planItem(
      planAssignment.group,
      planAssignment.course,
      scope,
      targetGroup,
      groupItems.length + 1,
    )];
    await savePlan(next, nextGroups);
    setCatalogPreviewClasses(previous => previous.filter(
      item => item.class_id !== planAssignment.course.class_id,
    ));
    setPlanAssignment(null);
    message.success(`已加入方案组“${targetGroup.name}”`);
  };

  const saveGroupEditor = async () => {
    if (!groupEditor?.name?.trim()) throw new Error('请填写方案组名称');
    const normalized = {
      group_id: groupEditor.group_id || `group_${Date.now().toString(36)}`,
      name: groupEditor.name.trim(),
      target_count: Number(groupEditor.target_count || 1),
    };
    const exists = planGroupConfigs.some(group => group.group_id === normalized.group_id);
    const nextGroups = exists
      ? planGroupConfigs.map(group => group.group_id === normalized.group_id ? normalized : group)
      : [...planGroupConfigs, normalized];
    const nextItems = plan.map(item => item.plan_group_id === normalized.group_id ? {
      ...item,
      plan_group_name: normalized.name,
      plan_group_target_count: normalized.target_count,
    } : item);
    await savePlan(nextItems, nextGroups);
    setTaskGroupIds(previous => previous.includes(normalized.group_id) || exists
      ? previous.filter(groupId => nextGroups.some(group => group.group_id === groupId))
      : [...previous, normalized.group_id]);
    setGroupEditor(null);
  };

  const conflictCandidates = useMemo(() => [
    ...plan,
    ...catalogPreviewClasses.filter(preview => (
      !plan.some(item => item.class_id === preview.class_id)
    )),
  ], [catalogPreviewClasses, plan]);

  const previewConflicts = async ({ silent = false } = {}) => {
    const meetings = conflictCandidates.flatMap(item => (item.schedules || []).map((meeting, index) => ({
      ...meeting, candidate_id: `${item.class_id}:${index}`, source_id: item.class_id,
      course_code: item.course_code, course_name: item.course_name,
      teaching_class_id: item.class_id, location: meeting.location || item.location,
      campus: meeting.campus_name || item.campus_name || meeting.campus || item.campus,
    })));
    if (!meetings.length || !termCode) {
      setConflicts({});
      return;
    }
    try {
      const result = await previewJwxkPlan({ batch_code: batchCode, term_code: termCode, meetings });
      const map = {};
      (result.results || []).forEach(item => {
        const classId = String(item.candidate_id).split(':')[0];
        const previous = map[classId];
        const rank = { clear: 0, unknown: 1, conflict: 2 };
        map[classId] = !previous || rank[item.status] > rank[previous.status]
          ? item
          : { ...previous, matches: [...(previous.matches || []), ...(item.matches || [])] };
      });
      setConflicts(map);
      if (!silent) message.success('已使用当前个人课表数据重新核验冲突');
    } catch (error) {
      if (!silent) message.error(error.message || '课表冲突检测失败');
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => previewConflicts({ silent: true }), 260);
    return () => window.clearTimeout(timer);
  }, [conflictCandidates, termCode]);

  const manualSelect = (group, course) => {
    const duplicate = selectedByCourseCode.get(String(course.course_code || '').toUpperCase());
    if (duplicate) {
      message.warning(`已选课程中已有“${duplicate.course_name || course.course_name}”，不能重复选择`);
      return;
    }
    let weight = 5;
    let minimumWeight = 5;
    let maximumWeight = 150;
    let weightBudget = null;
    const renderContent = ({ loading = false, error = '' } = {}) => (
      <div className="jwxk-manual-selection-confirm">
        <Paragraph>{course.teacher || '教师待定'} · {classScheduleText(course)} · {course.location || '地点待定'}</Paragraph>
        {batch?.selection_type_code === '04' && (
          <>
            {loading && <div className="jwxk-manual-weight-loading"><Spin size="small" /><Text type="secondary">正在读取官方剩余权重…</Text></div>}
            {error && <Alert type="error" showIcon message="剩余权重读取失败" description={error} />}
            {weightBudget && <Alert
              type="info"
              showIcon
              message={`当前剩余权重 ${weightBudget.remaining} 点`}
              description={`本次可投 ${minimumWeight}–${maximumWeight} 点，官方步长 ${Number(weightBudget.step || 1)} 点。`}
            />}
            <label className="jwxk-manual-weight-field"><span>本次投放权重</span><InputNumber
              min={minimumWeight}
              max={maximumWeight}
              step={Number(weightBudget?.step || 1)}
              defaultValue={weight}
              disabled={!weightBudget}
              onChange={value => { weight = Number(value || 0); }}
              addonAfter="点"
            /></label>
          </>
        )}
      </div>
    );
    const dialog = Modal.confirm({
      title: `确认选择“${course.course_name}”吗？`,
      content: renderContent({ loading: batch?.selection_type_code === '04' }),
      okText: batch?.selection_type_code === '04' ? '确认投权' : '确认选课', cancelText: '取消',
      okButtonProps: { disabled: batch?.selection_type_code === '04' },
      onOk: async () => {
        if (batch?.selection_type_code === '04') {
          if (!Number.isInteger(weight) || weight < minimumWeight || weight > maximumWeight) {
            message.error(`权重必须是 ${minimumWeight}–${maximumWeight} 的整数`);
            return Promise.reject(new Error('invalid weight'));
          }
        } else {
          weight = null;
        }
        const key = course.class_id; setActionLoading(key);
        try {
          const result = await selectJwxkCourse({ batch_code: batchCode, teaching_class_type: course.teaching_class_type || scope, class_id: course.class_id, course_code: course.course_code, weight, confirm_risk: true, preflight_verified: course.eligibility_status === 'selectable' });
          if (!result.success) {
            message.warning(result.message || '官方没有受理本次提交');
            return;
          }
          if (!result.queued) {
            const record = optimisticSelectionRecord(group, course, weight);
            upsertSelectedCourseLocally(record, {
              confirmed: record.selection_record_type === 'selected',
            });
          }
          message[result.queued ? 'info' : 'success'](
            result.queued
              ? (result.message || '已提交至官方队列，后台将继续核验')
              : (result.message || (batch?.selection_type_code === '04' ? '投权成功' : '选课成功')),
          );
          void verifySubmittedSelection(group, course, result);
        } catch (error) {
          message.error(error.message || '提交失败');
        } finally { setActionLoading(''); }
      },
    });
    if (batch?.selection_type_code === '04') {
      getJwxkWeightBudget(batchCode).then(result => {
        weightBudget = result;
        minimumWeight = Number(result.minimum || 5);
        maximumWeight = Math.min(150, Number(result.remaining || 0));
        weight = Math.min(maximumWeight, Math.max(minimumWeight, weight));
        const unavailable = maximumWeight < minimumWeight;
        dialog.update({
          content: renderContent(unavailable ? { error: `当前剩余 ${result.remaining || 0} 点，低于最低投放值 ${minimumWeight} 点` } : {}),
          okButtonProps: { disabled: unavailable },
        });
      }).catch(error => {
        dialog.update({
          content: renderContent({ error: error.message || '请稍后重试' }),
          okButtonProps: { disabled: true },
        });
      });
    }
  };

  const verifyThenSelect = async (group, course) => {
    if (course.eligibility_status === 'unavailable') {
      message.warning(course.eligibility_reason || '当前轮次不可选择这个教学班');
      return;
    }
    let verifiedCourse = course;
    if (course.eligibility_status !== 'selectable') {
      const [result] = await verifyEligibility([course.class_id]);
      if (!result) return;
      verifiedCourse = {
        ...course,
        eligibility_status: result.status,
        eligibility_reason: result.reason || '',
      };
      if (result.status === 'unavailable') {
        message.warning(result.reason || '当前轮次不可选择这个教学班');
        return;
      }
      if (result.status !== 'selectable') {
        message.warning(result.reason || '官方暂未返回明确的可选结果');
        return;
      }
    }
    manualSelect(group, verifiedCourse);
  };

  const confirmDeselect = course => {
    if (!isCurrentBatchSelectionRecord(course, batch?.selection_type_code)) {
      message.warning('这不是当前轮次的课程记录，不能在本轮执行退选');
      return;
    }
    Modal.confirm({
      title: `确认退选“${course.course_name}”吗？`,
      content: batch?.selection_type_code === '04' && course.selection_record_type === 'volunteered'
        ? `当前投入 ${course.devoted_weight ?? 0} 点权重。退选确认后，官方通常会返还本轮投入的权重。`
        : '官方明确返回成功后会立即从当前页面移除，后台再静默核验最终结果。',
      okButtonProps: { danger: true },
      okText: '确认退选',
      cancelText: '取消',
      onOk: async () => {
        setActionLoading(course.class_id);
        try {
          const result = await deselectJwxkCourse({
            batch_code: batchCode, class_id: course.class_id, selection_source: course.selection_source || '', confirm_risk: true,
          });
          if (!result.success) {
            message.warning(result.message || '官方没有受理本次退选');
            return;
          }
          if (!result.queued) removeSelectedCourseLocally(course);
          message[result.queued ? 'info' : 'success'](
            result.queued ? (result.message || '退选已进入官方队列') : (result.message || '退选成功'),
          );
          void verifyDeselectedCourse(course, { optimisticRemoved: !result.queued });
        } finally {
          setActionLoading('');
        }
      },
    });
  };

  const adjustCourseWeight = (group, course, selectedRecord) => {
    if (!isCurrentBatchSelectionRecord(selectedRecord, batch?.selection_type_code)) {
      message.warning('这不是当前轮次的投权记录，不能在本轮调整权重');
      return;
    }
    const oldWeight = Number(selectedRecord.devoted_weight || 0);
    let budget = null;
    let minimum = 5;
    let step = 1;
    let maximum = 150;
    let nextWeight = Math.max(minimum, oldWeight || minimum);
    const renderAdjustment = ({ loading = false, error = '' } = {}) => (
      <div className="jwxk-adjust-weight">
        <Alert
          type="warning"
          showIcon
          message="官方没有原地修改权重接口"
          description="确认后将先退选当前教学班；官方明确返回退选成功后立即按新权重重新投放，最终结果继续在后台核验。"
        />
        <div><span>当前权重</span><b>{oldWeight || '未返回'} 点</b></div>
        <div>
          <span>调整后权重</span>
          <InputNumber
            min={minimum}
            max={maximum}
            step={step}
            defaultValue={nextWeight}
            disabled={loading || Boolean(error)}
            onChange={value => { nextWeight = Number(value || 0); }}
            addonAfter="点"
          />
        </div>
        {loading && <Text type="secondary"><Spin size="small" /> 正在读取官方剩余权重…</Text>}
        {error && <Alert type="error" showIcon message="剩余权重读取失败" description={error} />}
        {budget && <Text type="secondary">可用上限 {maximum} 点（当前剩余 {budget.remaining} + 本课程退选后预计返还 {oldWeight}）</Text>}
      </div>
    );
    const dialog = Modal.confirm({
      title: `调整“${group.course_name}”的权重`,
      width: 560,
      content: renderAdjustment({ loading: true }),
      okText: '确认退选并重投',
      cancelText: '取消',
      okButtonProps: { disabled: true },
      onOk: async () => {
        if (!budget) return Promise.reject(new Error('budget pending'));
        if (!Number.isInteger(nextWeight) || nextWeight < minimum || nextWeight > maximum || (nextWeight - minimum) % step) {
          message.error(`权重需在 ${minimum}-${maximum} 之间，并按 ${step} 点递增`);
          return Promise.reject(new Error('invalid weight'));
        }
        if (nextWeight === oldWeight) {
          message.info('新权重与当前权重相同，无需调整');
          return;
        }
        setActionLoading(course.class_id);
        try {
          const removed = await deselectJwxkCourse({
            batch_code: batchCode, class_id: selectedRecord.class_id, selection_source: selectedRecord.selection_source || '', confirm_risk: true,
          });
          if (!removed.success) {
            message.warning(removed.message || '退选未成功，未执行重新投权');
            return;
          }
          let removalConfirmed = removed.success && !removed.queued;
          for (let attempt = 0; !removalConfirmed && attempt < 3; attempt += 1) {
            await new Promise(resolve => window.setTimeout(resolve, attempt === 0 ? 200 : 500));
            const result = await getJwxkSelected(batchCode, { includeMarket: false });
            const remaining = [...(result.selected || []), ...(result.volunteered || [])];
            removalConfirmed = !remaining.some(item => item.class_id === selectedRecord.class_id);
          }
          if (!removalConfirmed) {
            message.warning('退选已提交，但官方尚未确认移除；为避免重复占用权重，本次没有继续重投，请刷新后重试调整。');
            void verifyDeselectedCourse(selectedRecord);
            return;
          }
          removeSelectedCourseLocally(selectedRecord);
          const reapplied = await selectJwxkCourse({
            batch_code: batchCode,
            teaching_class_type: course.teaching_class_type || selectedRecord.teaching_class_type || scope,
            class_id: course.class_id,
            course_code: course.course_code || group.course_code,
            weight: nextWeight,
            confirm_risk: true,
            preflight_verified: true,
          });
          if (!reapplied.success) {
            message.warning(reapplied.message || '重新投权失败');
            return;
          }
          if (!reapplied.queued) {
            const record = optimisticSelectionRecord(group, course, nextWeight);
            upsertSelectedCourseLocally(record);
          }
          message[reapplied.queued ? 'info' : 'success'](
            reapplied.queued
              ? `已按 ${nextWeight} 点提交至官方队列，后台将继续核验`
              : `已按 ${nextWeight} 点重新投放`,
          );
          void verifySubmittedSelection(group, course, reapplied);
        } finally {
          setActionLoading('');
        }
      },
    });
    getJwxkWeightBudget(batchCode).then(result => {
      budget = result;
      minimum = Number(budget.minimum || 5);
      step = Number(budget.step || 1);
      maximum = Math.min(150, Number(budget.remaining || 0) + oldWeight);
      nextWeight = Math.min(maximum, Math.max(minimum, oldWeight || minimum));
      if (maximum < minimum) {
        dialog.update({
          content: renderAdjustment({ error: `当前可用权重不足，至少需要 ${minimum} 点` }),
          okButtonProps: { disabled: true },
        });
        return;
      }
      dialog.update({
        content: renderAdjustment(),
        okButtonProps: { disabled: false },
      });
    }).catch(error => {
      dialog.update({
        content: renderAdjustment({ error: error.message || '请稍后重试' }),
        okButtonProps: { disabled: true },
      });
    });
  };

  const weightModelItems = () => plan.map(item => {
    const existing = selectedByCourseCode.get(String(item.course_code || '').toUpperCase());
    return {
      ...item,
      utility: Number(item.utility || 5),
      course_already_selected: Boolean(existing),
      devoted_weight: existing?.devoted_weight ?? item.devoted_weight,
    };
  });

  const openWeightPlanner = async () => {
    if (!plan.length || !planGroups.length) return message.warning('请先配置方案组候选课程');
    try {
      const saved = await getJwxkWeightConfig(termCode);
      setGradeSizeDraft(saved.grade_size || null);
      // Each opening starts from the least surprising choice: include every
      // currently available plan group, then let the user narrow it here.
      setTaskGroupIds(planGroupConfigs.map(group => group.group_id));
      setWeightSetupOpen(true);
    } catch (error) {
      message.error(error.message || '读取策略配置失败');
    }
  };

  const buildWeightPlan = async () => {
    if (!gradeSizeDraft) return message.warning('请填写年级人数');
    const selectedGroupSet = new Set(taskGroupIds);
    const selectedGroups = planGroupConfigs.filter(group => selectedGroupSet.has(group.group_id));
    const selectedItems = weightModelItems().filter(item => selectedGroupSet.has(item.plan_group_id));
    if (!selectedGroups.length || !selectedItems.length) return message.warning('请至少选择一个包含候选课程的方案组');
    setWeightBuilding(true);
    try {
      const result = await planJwxkWeights({
        batch_code: batchCode,
        term_code: termCode,
        grade_size: Number(gradeSizeDraft),
        groups: selectedGroups,
        items: selectedItems,
      });
      setWeightPlan(result);
      setWeightSetupOpen(false);
    } catch (error) {
      message.error(error.message || '生成投权方案失败');
    } finally {
      setWeightBuilding(false);
    }
  };

  const applyWeights = () => {
    const items = weightPlan?.items || [];
    const used = items.reduce((sum, item) => sum + Number(item.weight || 0), 0);
    if (!items.length) return message.warning('当前策略没有可提交的课程');
    if (used > Number(weightPlan?.budget || 0)) return message.error(`当前方案使用 ${used} 点，超过官方剩余权重 ${weightPlan?.budget || 0}`);
    if (items.some(item => Number(item.weight || 0) < Number(weightPlan?.minimum || 5))) return message.error('存在低于官方最低值的课程权重');
    return Modal.confirm({
      title: '确认批量投放权重？', width: 620,
      content: <div>{items.map(item => <p key={item.class_id}><b>{item.course_name}</b> · {item.teacher || '教师待定'} · {item.weight} 点{item.reapply_required ? `（先撤回当前 ${item.current_weight ?? '-'} 点，再重新投放）` : ''}</p>)}</div>,
      okText: '确认并逐项提交', cancelText: '取消',
      onOk: async () => {
        const result = await applyJwxkWeights({ batch_code: batchCode, term_code: termCode, items });
        result.completed ? message.success('投权方案已全部提交至官方队列') : message.warning('批量投权已停止，请核验已完成项目');
        setWeightPlan(null);
      },
    });
  };

  const createWeightStrategyTask = async () => {
    if (batch?.selection_type_code !== '04') return message.error('策略投权只适用于权重选课轮次');
    if (!gradeSizeDraft) return message.warning('请先填写年级人数');
    const selectedGroupSet = new Set(taskGroupIds);
    const selectedGroups = planGroupConfigs.filter(group => selectedGroupSet.has(group.group_id));
    const selectedItems = plan.filter(item => selectedGroupSet.has(item.plan_group_id));
    if (!selectedGroups.length || !selectedItems.length) return message.warning('请至少选择一个包含候选课程的方案组');
    try {
      const created = await createJwxkAutomationTask({
        batch_code: batchCode,
        term_code: termCode,
        name: `${selectedGroups.length} 个方案组实时策略投权`,
        task_type: 'weight_strategy',
        start_at: batch?.begin_time || '',
        end_at: batch?.end_time || '',
        poll_seconds: 30,
        rebalance_seconds: 60,
        grade_size: Number(gradeSizeDraft),
        groups: selectedGroups,
        items: selectedItems.map(item => ({ ...item, utility: Number(item.utility || 5) })),
      });
      setWeightSetupOpen(false);
      setWeightPlan(null);
      setView('tasks');
      await loadTasks();
      const createdTaskId = String(created?.task_id || '');
      if (createdTaskId) {
        if (taskAttentionTimerRef.current) window.clearTimeout(taskAttentionTimerRef.current);
        setAttentionTaskId(createdTaskId);
        taskAttentionTimerRef.current = window.setTimeout(() => {
          setAttentionTaskId(current => current === createdTaskId ? '' : current);
          taskAttentionTimerRef.current = null;
        }, 3200);
      }
      message.success('实时策略任务已创建，请点击高亮的“启动实时策略”开始执行');
    } catch (error) {
      message.error(error.message || '创建实时策略投权任务失败');
    }
  };

  const createTask = async () => {
    if (batch?.selection_type_code !== '02') return message.error('自动抢课只适用于抢选轮次');
    const selectedGroupSet = new Set(taskGroupIds);
    const selectedGroups = planGroupConfigs.filter(group => selectedGroupSet.has(group.group_id));
    const selectedItems = plan.filter(item => selectedGroupSet.has(item.plan_group_id));
    if (!selectedItems.length || !selectedGroups.length) return message.warning('请至少选择一个包含候选课程的方案组');
    const invalidGroup = selectedGroups.map(group => ({ ...group, items: selectedItems.filter(item => item.plan_group_id === group.group_id) })).find(group => (
      new Set(group.items.map(item => item.course_code || item.class_id)).size < group.target_count
    ));
    if (invalidGroup) {
      message.error(`方案组“${invalidGroup.name}”的不同候选课程少于目标门数`);
      return;
    }
    try {
      await createJwxkAutomationTask({ batch_code: batchCode, term_code: termCode, name: `${selectedGroups.length} 个方案组自动抢课`, start_at: batch?.begin_time || '', end_at: batch?.end_time || '', poll_seconds: 15, groups: selectedGroups, items: selectedItems });
      message.success('已创建方案组任务；启动后各组会同时监测，组内按优先级选择'); setView('tasks');
    } catch (error) { message.error(error.message || '创建自动任务失败'); }
  };

  const openVacancySwap = course => {
    if (!selected.length) {
      message.warning('当前没有可配置为自动退选的已选课程');
      return;
    }
    setVacancySwapTarget(course);
    setVacancyDropIds([]);
  };

  const createVacancySwapTask = async () => {
    if (batch?.selection_type_code !== '02') return message.error('空位追踪只适用于抢选轮次');
    if (!vacancySwapTarget || !vacancyDropIds.length) return;
    const dropCourses = selected.filter(item => vacancyDropIds.includes(item.class_id));
    const groupId = `swap_${Date.now().toString(36)}`;
    const courseRef = item => ({
      class_id: item.class_id,
      course_code: item.course_code,
      course_name: item.course_name || '',
      teaching_class_type: item.teaching_class_type || 'ALLKC',
      teacher: item.teacher || '',
    });
    try {
      await createJwxkAutomationTask({
        batch_code: batchCode,
        term_code: termCode,
        name: `空位追踪 · ${vacancySwapTarget.course_name}`,
        task_type: 'vacancy_swap',
        groups: [],
        items: [],
        swap_groups: [{
          group_id: groupId,
          name: vacancySwapTarget.course_name,
          target: courseRef(vacancySwapTarget),
          drop_courses: dropCourses.map(courseRef),
        }],
        start_at: '', end_at: '', poll_seconds: 15,
      });
      setVacancySwapTarget(null);
      setVacancyDropIds([]);
      await loadTasks();
      setView('tasks');
      message.success('空位追踪组已创建；需要手动点击开始后才会执行');
    } catch (error) {
      message.error(error.message || '创建空位追踪组失败');
    }
  };

  const planScheduleOverlay = useMemo(
    () => plan
      .filter(item => !selected.some(selectedCourse => (
        isCurrentBatchSelectionRecord(selectedCourse, batch?.selection_type_code)
        && selectedCourse.selection_record_type === 'selected'
        && sameSelectionCourse(selectedCourse, item)
      )))
      .flatMap(item => scheduleOverlayForCourse(item, 'candidate', 'jwxk-plan')),
    [batch?.selection_type_code, plan, selected],
  );
  const previewScheduleOverlay = useMemo(() => (
    catalogPreviewClasses
      .filter(preview => !plan.some(item => item.class_id === preview.class_id))
      .flatMap(item => scheduleOverlayForCourse(item, 'preview', 'jwxk-preview'))
  ), [catalogPreviewClasses, plan]);
  const selectedScheduleOverlay = useMemo(() => (
    (schedule?.courses || [])
      .filter(item => (
        !(item.selection_record_type === 'volunteered'
          && plan.some(planItem => sameSelectionCourse(planItem, item)))
        && (
          item.selection_record_type === 'volunteered'
          || !personalCourses.some(personal => sameSelectionCourse(personal, item))
        )
      ))
      .flatMap(item => scheduleOverlayForCourse(
        item,
        item.selection_record_type === 'volunteered' ? 'pending' : 'selected',
        'jwxk-selected',
      ))
  ), [personalCourses, plan, schedule]);
  const candidateScheduleOverlay = useMemo(
    () => [...planScheduleOverlay, ...previewScheduleOverlay],
    [planScheduleOverlay, previewScheduleOverlay],
  );
  const catalogCourses = useMemo(() => groups.flatMap(group => (
    (group.classes || []).map(course => ({
      ...course,
      course_name: course.course_name || group.course_name,
      course_code: course.course_code || group.course_code,
    }))
  )), [groups]);
  const catalogScheduleOverlay = useMemo(
    () => catalogCourses.flatMap(item => scheduleOverlayForCourse(item, 'preview', 'jwxk-catalog')),
    [catalogCourses],
  );
  const catalogMeetingConflictMap = useMemo(
    () => immediateSelectionConflictMap(
      [...personalCourses, ...planScheduleOverlay],
      catalogScheduleOverlay,
    ),
    [catalogScheduleOverlay, personalCourses, planScheduleOverlay],
  );
  const catalogClassConflictMap = useMemo(
    () => summarizeSelectionConflictsByClass(
      catalogCourses.map(course => ({
        ...course,
        meetings: catalogScheduleOverlay.filter(meeting => meeting.source_id === course.class_id),
      })),
      catalogMeetingConflictMap,
      { baselineReady: personalScheduleReady },
    ),
    [catalogCourses, catalogMeetingConflictMap, catalogScheduleOverlay, personalScheduleReady],
  );
  const catalogGroupLiveStatsMap = useMemo(() => new Map(groups.map(group => [
    group.group_id,
    catalogGroupLiveStats(group, catalogClassConflictMap, batch?.selection_type_code),
  ])), [batch?.selection_type_code, catalogClassConflictMap, groups]);
  const allScheduleOverlay = useMemo(
    () => [...selectedScheduleOverlay, ...candidateScheduleOverlay],
    [candidateScheduleOverlay, selectedScheduleOverlay],
  );
  const immediateConflictMap = useMemo(
    () => immediateSelectionConflictMap(personalCourses, candidateScheduleOverlay),
    [candidateScheduleOverlay, personalCourses],
  );
  const overlayConflictMap = useMemo(() => {
    const result = { ...immediateConflictMap };
    candidateScheduleOverlay.forEach(meeting => {
      const realtime = conflicts[meeting.source_id || meeting.teaching_class_id];
      if (realtime) result[meeting.meeting_id] = realtime;
      const source = conflictCandidates.find(item => item.class_id === (meeting.source_id || meeting.teaching_class_id));
      if (source?.conflict && result[meeting.meeting_id]?.status !== 'conflict') {
        result[meeting.meeting_id] = {
          status: 'conflict',
          matches: [{
            baseline_meeting_id: `official-${source.class_id}`,
            baseline_course_name: source.conflict_description || '官方选课系统判定冲突',
            status: 'conflict', source: 'jwxk_official', overlapping_weeks: meeting.weeks || [],
            weekday: meeting.weekday, start_section: meeting.start_section, end_section: meeting.end_section,
          }],
        };
      }
    });
    return result;
  }, [candidateScheduleOverlay, conflictCandidates, conflicts, immediateConflictMap]);

  const handleSlotSelect = slot => {
    setTimeSlot(previous => (
      previous?.weekday === slot.weekday && previous?.section === slot.section ? null : slot
    ));
    setView('catalog');
    setPage(1);
    window.setTimeout(() => catalogRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  };

  const focusPlanCourse = async item => {
    const query = String(item.course_code || item.course_name || '').trim();
    if (!query) return;
    focusInProgressRef.current = true;
    setView('catalog');
    setScope('ALL');
    setAvailability('all');
    setWeekday('all');
    setTimeSlot(null);
    setCatalogFilters({ ...EMPTY_CATALOG_FILTERS });
    setPlanGapFilters({ ...EMPTY_CATALOG_FILTERS });
    setActivePlanGapId('');
    setActivePlanGapLabel('');
    setActivePlanGapCategoryMatched(true);
    setKeywordDraft(query);
    setKeyword(query);
    setPage(1);
    let loaded;
    try {
      loaded = await loadCatalog(
        1, query, 'ALL', null, { ...EMPTY_CATALOG_FILTERS }, 'all',
      );
    } finally {
      focusInProgressRef.current = false;
    }
    const normalizedCode = String(item.course_code || '').trim().toUpperCase();
    const normalizedName = String(item.course_name || '').trim().replace(/\s+/g, '');
    const target = (loaded || []).find(group => (
      (normalizedCode && String(group.course_code || '').trim().toUpperCase() === normalizedCode)
      || (!normalizedCode && String(group.course_name || '').trim().replace(/\s+/g, '') === normalizedName)
    ));
    if (!target) {
      message.warning('当前轮次目录未找到该课程，方案中的记录已保留');
      return;
    }
    setExpandedGroupId(target.group_id);
    setFocusedGroupId(target.group_id);
    window.setTimeout(() => {
      (courseClassRefs.current.get(item.class_id)
        || courseCardRefs.current.get(target.group_id))
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
    window.setTimeout(() => setFocusedGroupId(previous => (
      previous === target.group_id ? '' : previous
    )), 1800);
  };

  const toggleCatalogPreview = (group, course) => {
    const active = catalogPreviewClasses.some(item => item.class_id === course.class_id);
    const candidate = {
      ...course,
      course_name: group.course_name,
      course_code: group.course_code,
      catalog_group_id: group.group_id,
    };
    setCatalogPreviewClasses(previous => toggleCatalogPreviewCourse(previous, candidate));
    if (!active) {
      window.setTimeout(() => {
        scheduleRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 40);
    }
  };

  const cancelCatalogPreviewFromSchedule = course => {
    setCatalogPreviewClasses(previous => previous.filter(
      item => item.class_id !== course.class_id,
    ));
    setView('catalog');
    if (course.catalog_group_id) {
      setExpandedGroupId(course.catalog_group_id);
      setFocusedGroupId(course.catalog_group_id);
    }
    window.setTimeout(() => {
      (courseClassRefs.current.get(course.class_id)
        || courseCardRefs.current.get(course.catalog_group_id))
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
    window.setTimeout(() => setFocusedGroupId(previous => (
      previous === course.catalog_group_id ? '' : previous
    )), 1800);
  };

  const applyAcademicPlanGap = async gap => {
    const gapId = gap.wid || gap.path || gap.name;
    if (activePlanGapId === gapId) {
      setActivePlanGapId('');
      setActivePlanGapLabel('');
      setActivePlanGapCategoryMatched(true);
      setPlanGapFilters({ ...EMPTY_CATALOG_FILTERS });
      return;
    }
    let loadedOptions = filterOptions;
    try { loadedOptions = await ensureCatalogFilterOptions(); }
    catch (error) {
      // The category names already present in the plan and visible catalog are
      // still useful as a graceful fallback when the official dictionary is unavailable.
    }
    const matched = matchAcademicGapCatalogFilters(gap, {
      course_categories: mergedCategoryFilterOptions(
        loadedOptions?.course_categories,
        groups.flatMap(group => [
          group.normalized_course_category,
          ...(group.course_categories || []),
          group.course_category,
        ]),
      ),
      course_natures: mergedFilterOptions(
        loadedOptions?.course_natures,
        groups.map(group => group.course_nature),
      ),
      general_elective_categories: mergedFilterOptions(
        loadedOptions?.general_elective_categories,
        groups.map(group => group.general_elective_category),
      ),
    });
    if (!matched.courseCategory && !matched.courseNature && !matched.generalElectiveCategory) {
      message.warning('这个培养计划类别暂时无法对应到本轮课程分类');
      return;
    }
    const nextFilters = {
      courseNature: matched.courseNature,
      courseCategory: matched.courseCategory,
      generalElectiveCategory: matched.generalElectiveCategory,
      campus: '', department: '', startSection: '', endSection: '',
    };
    setActivePlanGapId(gapId);
    setActivePlanGapLabel(gap.name || gap.originalName || gap.path || '培养计划缺口');
    setActivePlanGapCategoryMatched(Boolean(matched.gapCategoryMatched));
    setPlanGapFilters(nextFilters);
    setExpandedGroupId('');
    setView('catalog');
    setPage(1);
    window.setTimeout(() => catalogRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  };

  const toggleCourseGroup = group => {
    const opening = expandedGroupId !== group.group_id;
    setExpandedGroupId(opening ? group.group_id : '');
    if (opening) {
      verifyEligibility((group.classes || [])
        .filter(course => course.eligibility_status === 'unknown')
        .map(course => course.class_id));
    }
  };

  const showCatalogDetail = async (group, course) => {
    const modal = Modal.info({
      title: `${group.course_name} · ${course.teacher || '教师待定'}`,
      width: 760,
      content: <div className="jwxk-detail-loading"><Spin /><span>正在读取课程与教学班详情…</span></div>,
    });
    try {
      const detail = await getJwxkCatalogDetail({
        batch_code: batchCode,
        teaching_class_type: course.teaching_class_type || scope,
        course_code: course.course_code || group.course_code,
        class_id: course.class_id,
      });
      const courseDetail = detail.course || {};
      const classDetail = detail.teaching_class || course;
      setGroups(previous => previous.map(item => item.group_id === group.group_id ? {
        ...item,
        ...Object.fromEntries(Object.entries(courseDetail).filter(([, value]) => (
          value !== '' && value != null && (!Array.isArray(value) || value.length)
        ))),
        classes: (item.classes || []).map(value => (
          value.class_id === classDetail.class_id ? { ...value, ...classDetail } : value
        )),
      } : item));
      const teacherText = (classDetail.teacher_details || []).map(item => (
        `${item.name}${item.title ? `（${item.title}）` : ''}`
      )).join('、') || classDetail.teacher_titles || classDetail.teacher || '教师待定';
      modal.update({
        content: (
          <div className="jwxk-class-detail">
            <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered title="课程信息">
              <Descriptions.Item label="课程代码">{courseDetail.course_code || group.course_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="英文名称">{courseDetail.english_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="学分 / 学时">{courseDetail.credits || group.credits || '-'} / {courseDetail.hours || group.hours || '-'}</Descriptions.Item>
              <Descriptions.Item label="开课学院">{courseDetail.department || group.department || '-'}</Descriptions.Item>
              <Descriptions.Item label="课程性质">{courseDetail.course_nature || group.course_nature || '-'}</Descriptions.Item>
              <Descriptions.Item label="课程类别">{(courseDetail.course_categories || group.course_categories || []).join('、') || courseDetail.course_category || group.course_category || '-'}</Descriptions.Item>
              {(courseDetail.general_elective_category || group.general_elective_category) && <Descriptions.Item label="通识选修课类别">{courseDetail.general_elective_category || group.general_elective_category}</Descriptions.Item>}
              {(classDetail.campus_name || classDetail.campus) && <Descriptions.Item label="校区">{campusLabel(classDetail.campus_name || classDetail.campus)}</Descriptions.Item>}
              <Descriptions.Item label="考试类型">{courseDetail.exam_type || group.exam_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="成绩分制">{courseDetail.score_scale || group.score_scale || '-'}</Descriptions.Item>
              {courseDetail.description && <Descriptions.Item label="课程简介" span={2}>{courseDetail.description}</Descriptions.Item>}
            </Descriptions>
            <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered title="教学班信息">
              <Descriptions.Item label="教学班">{classDetail.class_number || classDetail.class_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="教师">{teacherText}</Descriptions.Item>
              <Descriptions.Item label="教学形式">{classDetail.teaching_mode || '-'}</Descriptions.Item>
              <Descriptions.Item label={selectionParticipantLabel(classDetail, batch?.selection_type_code)}>{selectionParticipantCount(classDetail, batch?.selection_type_code) ?? '-'} / 容量 {classDetail.capacity ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="上课安排" span={2}>{classScheduleText(classDetail)}</Descriptions.Item>
              <Descriptions.Item label="地点" span={2}>{classDetail.location || '地点待定'}</Descriptions.Item>
              <Descriptions.Item label="面向班级" span={2}>{(classDetail.target_classes || []).join('、') || '-'}</Descriptions.Item>
              {classDetail.notice && <Descriptions.Item label="选课说明" span={2}>{classDetail.notice}</Descriptions.Item>}
              <Descriptions.Item label="官方完整安排" span={2}>{classDetail.official_schedule || '未提供'}</Descriptions.Item>
            </Descriptions>
          </div>
        ),
      });
    } catch (error) {
      modal.update({
        content: <Alert type="error" showIcon message="详情读取失败" description={error.message || '请稍后重试'} />,
      });
    }
  };

  const activeAdvancedFilters = Object.values(effectiveCatalogFilters).filter(Boolean).length;
  const catalog = (
    <Spin spinning={loading || (availability === 'selectable' && eligibilityLoading.length > 0)}>
      <div className="jwxk-catalog-layout" ref={catalogRef}>
        <section>
          <div className="jwxk-search-row">
            <Input.Search
              allowClear
              enterButton="搜索"
              prefix={<SearchOutlined />}
              value={keywordDraft}
              onChange={event => setKeywordDraft(event.target.value)}
              onSearch={value => setKeyword(value.trim())}
              loading={loading}
              placeholder="输入完成后按回车或点击搜索"
            />
            <Select value={scope} options={scopeOptions.map(item => ({ value: item.code, label: item.name && item.name !== item.code ? item.name : courseScopeLabel(item.code) }))} onChange={setScope} />
            <Select value={availability} onChange={setAvailability} options={[{ value: 'all', label: '全部状态' }, { value: 'selectable', label: '本轮可选' }, { value: 'available', label: batch?.selection_type_code === '04' ? '当前未超容量' : '仍有余量' }, { value: 'conflict_free', label: '官方无冲突' }, { value: 'selected', label: '已经选择' }]} />
            <Select value={weekday} onChange={setWeekday} options={[{ value: 'all', label: '全部星期' }, ...WEEKDAYS.map((label, index) => ({ value: String(index + 1), label: `周${label}` }))]} />
            <Button onClick={openCatalogFilters} loading={filterLoading}>更多筛选{activeAdvancedFilters ? ` ${activeAdvancedFilters}` : ''}</Button>
          </div>
          {batch?.state === 'active' && (
            <div className="jwxk-capacity-refresh-status">
              <Badge status={capacityRefreshing ? 'processing' : 'success'} />
              <Text type="secondary">
                {capacityRefreshing ? '正在更新学校端人数数据' : `${batch?.selection_type_code === '04' ? '已投注人数' : '已选人数'}与容量每 30 秒静默更新`}
                {capacityUpdatedAt ? ` · 最近更新 ${capacityUpdatedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : ''}
              </Text>
            </div>
          )}
          {(timeSlot || activeAdvancedFilters || activePlanGapId) && (
            <div className="jwxk-active-filters">
              {timeSlot && <Tag closable onClose={() => setTimeSlot(null)} color="blue">周{WEEKDAYS[timeSlot.weekday - 1]} · 覆盖第{timeSlot.section}节</Tag>}
              {Object.entries(catalogFilters).filter(([, value]) => value).map(([key, value]) => (
                <Tag key={`manual-${key}`} closable onClose={() => setCatalogFilters(previous => ({ ...previous, [key]: '' }))}>{FILTER_LABELS[key]} · {filterValueLabel(key, value)}</Tag>
              ))}
              {activePlanGapId && (
                <Tooltip title="这是你选择的培养计划缺口；其后的紫色标签是本轮课程目录实际可执行的映射条件">
                  <Tag color={activePlanGapCategoryMatched ? 'purple' : 'warning'} closable onClose={() => { setActivePlanGapId(''); setActivePlanGapLabel(''); setActivePlanGapCategoryMatched(true); setPlanGapFilters({ ...EMPTY_CATALOG_FILTERS }); }}>培养计划缺口 · {activePlanGapLabel || '当前类别'}{activePlanGapCategoryMatched ? '' : ' · 本轮无对应分类'}</Tag>
                </Tooltip>
              )}
              {activePlanGapId && Object.entries(planGapFilters).filter(([key, value]) => value && !catalogFilters[key]).map(([key, value]) => (
                <Tag color="purple" key={`plan-${key}`} closable onClose={() => { setActivePlanGapId(''); setActivePlanGapLabel(''); setActivePlanGapCategoryMatched(true); setPlanGapFilters({ ...EMPTY_CATALOG_FILTERS }); }}>培养计划 · {FILTER_LABELS[key]} · {filterValueLabel(key, value)}</Tag>
              ))}
              {!activePlanGapCategoryMatched && activePlanGapId && (
                <Alert
                  className="jwxk-plan-gap-mapping-warning"
                  type="warning"
                  showIcon
                  message={`本轮课程目录没有“${activePlanGapLabel}”这一细分类`}
                  description="当前只能按下方显示的上级条件检索；如果课程列表为空，表示本轮没有提供对应课程，不是筛选加载失败。"
                />
              )}
              <Button type="link" size="small" onClick={() => { setActivePlanGapId(''); setActivePlanGapLabel(''); setActivePlanGapCategoryMatched(true); setPlanGapFilters({ ...EMPTY_CATALOG_FILTERS }); setTimeSlot(null); setCatalogFilters({ ...EMPTY_CATALOG_FILTERS }); }}>清除筛选</Button>
            </div>
          )}
          <div className="jwxk-group-list">
            {visibleGroups.map(group => {
              const expanded = expandedGroupId === group.group_id;
              const liveStats = catalogGroupLiveStatsMap.get(group.group_id) || {
                conflict_free_count: 0,
                all_classes_conflict: false,
                available_count: 0,
              };
              const hasVolunteeredClass = volunteeredCourseCodes.has(
                String(group.course_code || '').trim().toUpperCase(),
              ) || (group.classes || []).some(course => (
                volunteeredClassIds.has(String(course.class_id || '').trim())
                || volunteeredCourseCodes.has(
                  String(course.course_code || '').trim().toUpperCase(),
                )
              ));
              return (
                <Card
                  key={group.group_id}
                  ref={node => {
                    if (node) courseCardRefs.current.set(group.group_id, node);
                    else courseCardRefs.current.delete(group.group_id);
                  }}
                  className={`jwxk-course-group${expanded ? ' is-expanded' : ''}${focusedGroupId === group.group_id ? ' is-focused' : ''}${hasVolunteeredClass ? ' has-volunteered-class' : ''}${liveStats.all_classes_conflict ? ' has-all-conflicts' : ''}`}
                  role="button"
                  tabIndex={0}
                  aria-expanded={expanded}
                  onClick={() => toggleCourseGroup(group)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      toggleCourseGroup(group);
                    }
                  }}
                >
                  <div className="jwxk-course-group__head">
                    <div className="jwxk-course-group__main">
                      <Space wrap>
                        {uniqueDisplayLabels(group.source_tags, courseScopeLabel).map(label => (
                          <Tag color={label === '培养方案内课' ? 'blue' : 'default'} key={label}>{label}</Tag>
                        ))}
                        {(group.normalized_course_category || group.course_category) && <Tag color="geekblue">类别 · {group.normalized_course_category || group.course_category}</Tag>}
                        {group.course_nature && <Tag>性质 · {group.course_nature}</Tag>}
                      </Space>
                      <div className="jwxk-course-group__title-row">
                        <Title level={4}>{group.course_name}</Title>
                        <div className="jwxk-course-group__facts">
                          <span>成绩分制 <b>{group.score_scale || '待确认'}</b></span>
                          <span>学分 <b>{group.credits || '-'}</b></span>
                          <span>学院 <b>{group.department || '待定'}</b></span>
                          <span>考试类型 <b>{group.exam_type || '待确认'}</b></span>
                          {group.general_elective_category && <span>通识类别 <b>{group.general_elective_category}</b></span>}
                          {(group.campuses || []).length > 0 && <span>校区 <b>{group.campuses.map(campusLabel).join('、')}</b></span>}
                        </div>
                      </div>
                      <Text type="secondary">{group.course_code || '课程代码待定'}</Text>
                    </div>
                    <Badge count={group.class_count} overflowCount={99} />
                  </div>
                  <div className="jwxk-course-group__stats">
                    <span>{liveStats.conflict_free_count} 个无冲突教学班</span>
                    <span>{liveStats.available_count} 个有容量教学班</span>
                    <b>{expanded ? '收起教学班' : '比较教学班'}</b>
                  </div>
                  {expanded && (
                    <div className="jwxk-inline-classes" onClick={event => event.stopPropagation()}>
                      {(group.classes || []).map(course => {
                        const conflict = conflicts[course.class_id] || catalogClassConflictMap[course.class_id];
                        const conflictStatus = course.conflict ? 'conflict' : conflict?.status;
                        const duplicate = selectedByCourseCode.get(String(course.course_code || group.course_code || '').toUpperCase())
                          || (course.course_already_selected ? { course_name: group.course_name } : null);
                        const selectedRecord = selectedByClassId.get(String(course.class_id));
                        const selectedRecordIsCurrent = !selectedRecord
                          || isCurrentBatchSelectionRecord(selectedRecord, batch?.selection_type_code);
                        const inPlan = plan.some(item => item.class_id === course.class_id);
                        const manuallyPreviewed = catalogPreviewClasses.some(
                          item => item.class_id === course.class_id,
                        );
                        return (
                          <article
                            key={course.class_id}
                            ref={node => {
                              if (node) courseClassRefs.current.set(course.class_id, node);
                              else courseClassRefs.current.delete(course.class_id);
                            }}
                            className={`jwxk-inline-class${inPlan || manuallyPreviewed ? ' is-previewing' : ''}${conflictStatus === 'conflict' ? ' has-conflict' : ''}`}
                            tabIndex={0}
                          >
                            <div className="jwxk-inline-class__summary">
                              <strong>{course.teacher || '教师待定'}</strong>
                              <span>{classScheduleText(course)}</span>
                              <small>{selectionParticipantLabel(course, batch?.selection_type_code)} {selectionParticipantCount(course, batch?.selection_type_code) ?? '-'} / 容量 {course.capacity ?? '-'}</small>
                            </div>
                            <Space wrap className="jwxk-inline-class__states">
                              {course.full && <Tag>已满</Tag>}
                              {course.restricted && <Tag color="warning">受限</Tag>}
                              {course.eligibility_status === 'selectable' && <Tag color="success">本轮可选</Tag>}
                              {course.eligibility_status === 'unavailable' && (
                                <Tooltip title={course.eligibility_reason || '当前轮次不可选择'}><Tag color="error">本轮不可选</Tag></Tooltip>
                              )}
                              {course.eligibility_status === 'unknown' && <Tag>可选性待核验</Tag>}
                              {selectedRecord?.selection_record_type === 'volunteered' && <Tag color="purple">已投权 {selectedRecord.devoted_weight ?? 0} 点</Tag>}
                              {selectedRecord?.selection_record_type === 'selected' && <Tag color="blue">已选中</Tag>}
                              {selectedRecord && !selectedRecordIsCurrent && <Tag>非本轮课程</Tag>}
                              {duplicate && !selectedRecord && <Tag color="blue">同课程已选或已投</Tag>}
                              {conflictStatus === 'conflict' && <Tag color="error">课程冲突</Tag>}
                              {conflictStatus === 'unknown' && <Tag color="warning">时间待核验</Tag>}
                              {conflictStatus === 'clear' && <Tag color="success">无冲突</Tag>}
                            </Space>
                            <Space wrap className="jwxk-inline-class__actions">
                              <Button size="small" onClick={() => showCatalogDetail(group, course)}>查看详情</Button>
                              <Button
                                size="small"
                                icon={<BookOutlined />}
                                disabled={!group.course_code && !course.course_code}
                                onClick={() => setOutlineCourse({
                                  course_code: group.course_code || course.course_code,
                                  course_name: group.course_name || course.course_name,
                                })}
                              >查看大纲</Button>
                              <Button
                                size="small"
                                disabled={inPlan}
                                onClick={() => toggleCatalogPreview(group, course)}
                              >{inPlan ? '已在方案课表中' : manuallyPreviewed ? '取消课表预览' : '在课表中预览'}</Button>
                              <Button size="small" onClick={() => openPlanAssignment(group, course)} icon={<ShoppingCartOutlined />}>加入方案组</Button>
                              {batch?.selection_type_code === '02' && course.full && !duplicate && <Button size="small" icon={<SwapOutlined />} onClick={() => openVacancySwap({ ...course, course_name: group.course_name, course_code: group.course_code })}>追踪空位换课</Button>}
                              {selectedRecord && selectedRecordIsCurrent && batch?.selection_type_code === '04' && selectedRecord.selection_record_type === 'volunteered' && (
                                <Button size="small" onClick={() => adjustCourseWeight(group, course, selectedRecord)}>调整权重</Button>
                              )}
                              {selectedRecord && selectedRecordIsCurrent && <Button danger size="small" loading={actionLoading === course.class_id} onClick={() => confirmDeselect(selectedRecord)}>退选</Button>}
                              {!selectedRecord && pendingVerificationClassIds.includes(course.class_id) && <Tag color="processing">等待官方结果确认</Tag>}
                              {!selectedRecord && <Tooltip title={duplicate ? `同课程已有“${duplicate.course_name || group.course_name}”` : pendingVerificationClassIds.includes(course.class_id) ? '本次提交正在核验，请勿重复操作' : !batch?.can_enter ? '当前不在选课时间内' : course.full ? '容量已满' : course.restricted ? '当前账号受限' : course.eligibility_status === 'unavailable' ? (course.eligibility_reason || '当前轮次不可选择') : ''}>
                                <Button
                                  type="primary"
                                  size="small"
                                  disabled={Boolean(duplicate) || pendingVerificationClassIds.includes(course.class_id) || !batch?.can_enter || course.full || course.restricted || course.has_test || course.eligibility_status === 'unavailable'}
                                  loading={actionLoading === course.class_id || eligibilityLoading.includes(course.class_id)}
                                  onClick={() => verifyThenSelect(group, course)}
                                ><CheckCircleOutlined />{course.eligibility_status === 'unknown'
                                  ? (batch?.selection_type_code === '04' ? '核验并投权' : '核验并选择')
                                  : batch?.selection_type_code === '04' ? '投放权重' : '立即选课'}</Button>
                              </Tooltip>}
                            </Space>
                            {course.has_test && <Alert type="warning" showIcon message="该课程需要配套实验班；当前不会在未选择实验班时直接提交。" />}
                          </article>
                        );
                      })}
                      <Button className="jwxk-collapse-classes" type="text" onClick={() => {
                        setExpandedGroupId('');
                      }}>收起教学班</Button>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
          {!loading && !visibleGroups.length && <Empty description="当前条件下没有课程" />}
          {total > 20 && <Pagination current={page} total={total} pageSize={20} showSizeChanger={false} onChange={next => loadCatalog(next)} />}
        </section>
        <aside className="jwxk-plan-aside">
          <section className="jwxk-plan-gap-panel" aria-label="培养计划缺口">
            <div className="jwxk-plan-gap-panel__head">
              <span><BookOutlined /><b>培养计划缺口</b></span>
              {academicReportResource.data && <Tag>{visibleAcademicPlanGaps.length} 项</Tag>}
            </div>
            {academicReportResource.data?.program_name && <small className="jwxk-plan-gap-program">{academicReportResource.data.program_name}</small>}
            {academicReportResource.data && academicPlanSelected.length > 0 && (
              <small className="jwxk-plan-gap-program">
                已实时计入本轮已选或已投权的 {academicPlanProjection.matched.length} 门课程
                {academicPlanProjection.unmatched.length > 0 ? `；${academicPlanProjection.unmatched.length} 门因缺少可靠类别未计入` : ''}
              </small>
            )}
            {academicReportResource.loading && !academicReportResource.data && <div className="jwxk-plan-gap-loading"><Spin size="small" /><span>读取培养计划缓存…</span></div>}
            {visibleAcademicPlanGaps.map(gap => {
              const gapId = gap.wid || gap.path || gap.name;
              const unfinished = (gap.unfinished_courses || []).slice(0, 3).map(course => course.course_name).filter(Boolean);
              return (
                <button
                  type="button"
                  className={`jwxk-plan-gap${activePlanGapId === gapId ? ' is-active' : ''}`}
                  key={gapId}
                  onClick={() => applyAcademicPlanGap(gap)}
                >
                  <span><strong>{gap.name}</strong><Tag color={gap.requirement_type === 'elective' ? 'blue' : 'default'}>{gap.requirement_type === 'elective' ? '选修' : gap.requirement_type === 'required' ? '必修' : '综合'}</Tag></span>
                  <b>{getAcademicRuleDeficitText(gap)}</b>
                  {gap.external_selected_credits > 0 && <small>本轮实时已选已抵扣 {gap.external_selected_credits} 学分</small>}
                  {unfinished.length > 0 && <small>待修：{unfinished.join('、')}{gap.unfinished_courses.length > unfinished.length ? ' 等' : ''}</small>}
                  <small>{gap.path || '点击按课程类别与性质查找'}</small>
                </button>
              );
            })}
            {academicReportResource.data && !visibleAcademicPlanGaps.length && <div className="jwxk-plan-gap-empty">当前轮次对应类型下没有待选缺口</div>}
            {!academicReportResource.data && !academicReportResource.loading && (
              <div className="jwxk-plan-gap-empty">
                <span>{academicReportResource.syncError || academicReportResource.error?.message || '尚未保存培养计划'}</span>
                <Button size="small" type="link" onClick={() => academicReportResource.refresh().catch(error => message.error(error.message || '培养计划更新失败'))}>现在加载</Button>
              </div>
            )}
            {academicReportResource.updateAvailable && <Button size="small" type="link" onClick={academicReportResource.applyAvailable}>使用刚更新的培养计划</Button>}
          </section>
          <div><ShoppingCartOutlined /><b>我的方案</b><Tag>{planGroups.length} 组 / {plan.length} 个备选</Tag></div>
          {planGroups.map(group => <div className="jwxk-plan-aside-group" key={group.id}><strong>{group.name} · 目标 {group.target_count} 门</strong>{group.items.map(item => <button type="button" className="jwxk-plan-course-link" key={item.class_id} onClick={() => focusPlanCourse(item)}>{item.priority}. {item.course_name} · {item.teacher || '教师待定'}</button>)}</div>)}
          <Space direction="vertical" className="jwxk-plan-aside-actions"><Button block onClick={() => setGroupEditor({ group_id: '', name: '', target_count: 1 })}>新建方案组</Button><Button block onClick={() => setView('plan')}>管理方案组</Button></Space>
        </aside>
      </div>
    </Spin>
  );

  const planView = <div className="jwxk-plan-page"><div className="jwxk-section-actions"><Button onClick={() => setGroupEditor({ group_id: '', name: '', target_count: 1 })}>新建方案组</Button><Button icon={<CalendarOutlined />} onClick={previewConflicts}>实时检查冲突</Button>{batch?.selection_type_code !== '04' && planGroups.length > 0 && <Checkbox.Group className="jwxk-task-group-picker" value={taskGroupIds} onChange={setTaskGroupIds} options={planGroups.map(group => ({ value: group.group_id, label: `自动抢课：${group.name}` }))} />}{batch?.selection_type_code !== '04' && <Button type="primary" icon={<RobotOutlined />} disabled={!plan.length || !planGroups.length} onClick={createTask}>创建所选方案组自动任务</Button>}{batch?.selection_type_code === '04' && <Button type="primary" icon={<RobotOutlined />} onClick={openWeightPlanner}>策略投权</Button>}</div>{planGroups.map(group => <Card key={group.id} title={`${group.name} · 目标 ${group.target_count} 门 · ${group.items.length} 个候选`} extra={<Space><Button size="small" onClick={() => setGroupEditor({ group_id: group.group_id, name: group.name, target_count: group.target_count })}>编辑目标</Button><Button size="small" danger onClick={() => Modal.confirm({ title: `删除方案组“${group.name}”？`, content: '组内候选课程也会一起移除。', okButtonProps: { danger: true }, okText: '删除', onOk: () => { setTaskGroupIds(previous => previous.filter(groupId => groupId !== group.group_id)); return savePlan(plan.filter(item => item.plan_group_id !== group.group_id), planGroupConfigs.filter(item => item.group_id !== group.group_id)); } })}>删除组</Button></Space>} className="jwxk-plan-group">{group.items.map(item => {
    const conflict = conflicts[item.class_id];
    return <div className="jwxk-plan-alternative" key={item.class_id}><div><button type="button" className="jwxk-plan-course-link is-primary" onClick={() => focusPlanCourse(item)}>{item.priority}. {item.course_name || item.course_code || '未命名课程'}</button><strong>{item.course_code || '课程代码待定'} · {item.teacher || '教师待定'} · {item.class_number || item.class_id}</strong><span>{item.location || '地点待定'} · {classScheduleText(item)}</span></div><Space wrap className="jwxk-plan-alternative__actions">{conflict && <Tag color={conflict.status === 'conflict' ? 'error' : conflict.status === 'unknown' ? 'warning' : 'success'}>{conflict.status === 'conflict' ? '冲突' : conflict.status === 'unknown' ? '待核验' : '无冲突'}</Tag>}<InputNumber min={1} max={group.items.length} value={item.priority} onChange={value => savePlan(plan.map(row => row.class_id === item.class_id ? { ...row, priority: value || 1 } : row))} addonBefore="优先级" />{batch?.selection_type_code === '04' && <InputNumber min={1} max={10} value={item.utility || 5} onChange={value => savePlan(plan.map(row => row.class_id === item.class_id ? { ...row, utility: value || 5 } : row))} addonBefore="意愿" />}<Button danger onClick={() => savePlan(plan.filter(row => row.class_id !== item.class_id))}>移出方案组</Button></Space></div>;
  })}</Card>)}{!plan.length && <Empty description="从课程目录选择教学班加入方案组" />}</div>;

  const selectedView = <Spin spinning={loading}><Alert type="info" showIcon message={schedule?.source_label || '官方实时选课结果'} description={<span>{selectedRefreshing ? '正在静默更新人数状态' : '人数状态每 30 秒静默更新'}{selectedUpdatedAt ? ` · 最近更新 ${selectedUpdatedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : ''}</span>} /><div className="jwxk-selected-grid">{orderedSelected.map(course => {
    const participantCount = selectionParticipantCount(course, batch?.selection_type_code);
    const participantLabel = selectionParticipantLabel(course, batch?.selection_type_code);
    const capacity = course.capacity == null ? null : Number(course.capacity);
    const currentBatchRecord = isCurrentBatchSelectionRecord(course, batch?.selection_type_code);
    const delta = participantCount != null && capacity != null ? Number(participantCount) - capacity : null;
    const statusText = !currentBatchRecord
      ? '非本轮课程'
      : participantCount == null || capacity == null
      ? '人数待更新'
      : batch?.selection_type_code === '04'
        ? (delta > 0 ? `超过容量 ${delta} 人` : '当前在容量范围内')
        : (delta >= 0 ? '已满' : `剩余 ${Math.abs(delta)} 个名额`);
    return <Card key={course.class_id}><Title level={5}>{course.course_name}</Title><Paragraph>{course.teacher || '教师待定'} · {course.location || '地点待定'}</Paragraph><Text type="secondary">{classScheduleText(course)}</Text><div className="jwxk-selected-market"><b>{participantLabel} {participantCount ?? '-'} / 容量 {capacity ?? '-'}</b><Tag color={!currentBatchRecord || participantCount == null || capacity == null ? 'default' : delta > 0 || (batch?.selection_type_code !== '04' && delta >= 0) ? 'warning' : 'success'}>{statusText}</Tag></div>{course.selection_record_type === 'volunteered' && currentBatchRecord && <Tag color="purple">当前投权 {course.devoted_weight ?? 0} 点</Tag>}{!currentBatchRecord && <Paragraph type="secondary">该记录不属于当前轮次，仅供查看。</Paragraph>}{currentBatchRecord && <Space direction="vertical" style={{ width: '100%' }}>{batch?.selection_type_code === '04' && course.selection_record_type === 'volunteered' && <Button block onClick={() => adjustCourseWeight(course, course, course)}>调整权重</Button>}<Button danger block loading={actionLoading === course.class_id} onClick={() => confirmDeselect(course)}>退选</Button></Space>}</Card>;
  })}</div>{!selected.length && !loading && <Empty description="当前轮次暂无已选课程" />}</Spin>;

  const taskView = <div className="jwxk-task-list">
    <Alert
      type="info"
      showIcon
      message={<Space><Badge status={tasksRefreshing ? 'processing' : 'success'} />后台任务实时状态</Space>}
      description="停留在本页时每秒读取一次本地执行状态；学校端人数仍按任务自身的安全轮询间隔更新。关闭页面不会停止任务。"
    />
    {tasks.map(task => {
    const isSwap = task.task_type === 'vacancy_swap';
    const isWeight = task.task_type === 'weight_strategy';
    const entries = isSwap ? Object.entries(task.swap_results || {}) : Object.entries(task.group_results || {});
    const recommendationByClass = new Map((task.weight_status?.recommendation || []).map(item => [String(item.class_id), item]));
    const pendingDropClassIds = new Set((task.weight_status?.pending_drop || []).map(item => String(item.class_id || '')));
    const inflightWeight = task.weight_status?.inflight || null;
    const historyCourseByClass = new Map([
      ...(task.items || []),
      ...(task.swap_groups || []).flatMap(group => [group.target, ...(group.drop_courses || [])]),
    ].filter(Boolean).map(item => [String(item.class_id || ''), item]));
    const historyCourseLabel = result => {
      const course = historyCourseByClass.get(String(result.class_id || '')) || {};
      const name = result.course_name || course.course_name || '';
      const code = result.course_code || course.course_code || '';
      return [name, code].filter(Boolean).join('-') || result.class_id || '课程信息待确认';
    };
    const nextAttemptSeconds = task.next_attempt_at
      ? Math.max(0, Math.ceil((new Date(task.next_attempt_at).getTime() - Date.now()) / 1000))
      : null;
    const active = ['running', 'waiting'].includes(task.status);
    const execution = task.execution || {};
    const executionElapsedSeconds = execution.state === 'running' && execution.check_started_at
      ? Math.max(0, Math.round((Date.now() - new Date(execution.check_started_at).getTime()) / 1000))
      : execution.last_duration_ms != null ? Math.round(Number(execution.last_duration_ms) / 100) / 10 : null;
    const executionEvents = [...(execution.events || [])].slice(-5).reverse();
      return <Card key={task.task_id} className={active ? 'jwxk-task-card is-live' : 'jwxk-task-card'}>
        <div className="jwxk-task-head"><div><Title level={5}>{task.name}</Title><div className="jwxk-task-live-log"><Text type={execution.state === 'error' ? 'danger' : 'secondary'}>{task.message}</Text>{execution.state === 'running' && <small>当前阶段已执行 {executionElapsedSeconds ?? 0} 秒</small>}{executionEvents.length > 0 && <div className="jwxk-task-live-log__events">{executionEvents.map((event, index) => <span className={event.level === 'error' ? 'is-error' : ''} key={`${event.at || index}:${event.stage_code || ''}`}><time>{event.at ? new Date(event.at).toLocaleTimeString('zh-CN', { hour12: false }) : '--:--:--'}</time>{event.message}</span>)}</div>}</div></div><Tag color={active ? 'processing' : task.status === 'success' ? 'success' : task.status === 'needs_review' ? 'warning' : 'default'}>{task.status === 'running' ? '运行中' : task.status === 'waiting' ? '等待并自动重试' : task.status === 'success' ? '已完成' : task.status === 'needs_review' ? '待人工核验' : task.status === 'paused' ? '已暂停' : '草稿'}</Tag></div>
        <div className="jwxk-task-runtime">
          <span><b>{task.attempt_count || 0}</b><small>检查次数</small></span>
          <span><b>{task.poll_interval_seconds || task.poll_seconds || 15}s</b><small>学校端轮询</small></span>
          <span><b>{formatTaskTimestamp(task.last_attempt_at)}</b><small>最近检查</small></span>
          <span><b>{active && nextAttemptSeconds != null ? `${nextAttemptSeconds}s` : '-'}</b><small>预计下次检查</small></span>
        </div>
        <div className="jwxk-task-groups">{entries.map(([groupId, result]) => {
      const group = isSwap
        ? (task.swap_groups || []).find(item => item.group_id === groupId)
        : (task.groups || []).find(item => item.group_id === groupId) || (task.items || []).find(item => (item.plan_group_id || item.course_code || item.class_id) === groupId);
      const groupItems = isSwap ? [group?.target].filter(Boolean) : (task.items || []).filter(item => (
        (item.plan_group_id || item.course_code || item.class_id) === groupId
      ));
      return <section className="jwxk-task-group" key={groupId}>
        <div className="jwxk-task-group-summary"><span>{group?.name || group?.plan_group_name || group?.course_name || groupId}{isSwap && group?.drop_courses?.length ? `（空位后退 ${group.drop_courses.map(item => item.course_name).join('、')}）` : ''}</span><Tag color={result.status === 'success' ? 'success' : result.status === 'needs_review' ? 'warning' : ['verifying', 'verifying_drop'].includes(result.status) ? 'processing' : 'default'}>{result.status === 'success' ? '已完成' : result.status === 'needs_review' ? '待核验' : result.status === 'verifying_drop' ? '确认退选中' : result.status === 'verifying' ? '确认选课中' : isSwap ? '追踪空位中' : '监测中'}</Tag>{!isSwap && <b>{result.success_count || 0}/{result.target_count || group?.target_count || 1} 门</b>}<small>{result.message}</small></div>
        <div className="jwxk-task-courses">{groupItems.map(item => {
          const state = task.course_states?.[item.class_id] || {};
          const recommendation = recommendationByClass.get(String(item.class_id)) || {};
          const merged = { ...item, ...recommendation, ...state };
          const participantCount = selectionParticipantCount(merged, isWeight ? '04' : '02');
          const participantLabel = selectionParticipantLabel(merged, isWeight ? '04' : '02');
          const stateHasCurrentWeight = Object.prototype.hasOwnProperty.call(state, 'devoted_weight');
          const currentWeight = stateHasCurrentWeight
            ? state.devoted_weight
            : recommendation.current_weight ?? recommendation.devoted_weight ?? item.devoted_weight;
          const recommendedWeight = recommendation.weight;
          const classId = String(item.class_id || '');
          const inflightForCourse = String(inflightWeight?.class_id || '') === classId ? inflightWeight : null;
          const explicitAction = recommendation.action;
          const recommendationText = inflightForCourse?.action === 'drop'
            ? '正在撤回'
            : inflightForCourse?.action === 'add'
              ? `正在投放 ${inflightForCourse.weight ?? recommendedWeight ?? ''} 点`
              : pendingDropClassIds.has(classId) || explicitAction === 'drop'
                ? '建议撤回'
                : explicitAction === 'keep'
                  ? `保持 ${recommendedWeight ?? currentWeight} 点`
                  : explicitAction === 'change'
                    ? `调整为 ${recommendedWeight} 点`
                    : explicitAction === 'add'
                      ? `投放 ${recommendedWeight} 点`
                      : ['out', 'alternative'].includes(explicitAction)
                        ? '本轮不投'
                        : recommendation.class_id && recommendedWeight > 0
                          ? `${recommendedWeight} 点`
                          : execution.state === 'running'
                            ? '计算中'
                            : task.weight_status?.last_calculated_at ? '待重新计算' : '待计算';
          const classificationText = recommendation.classification === 'SAFE'
            ? '容量安全'
            : recommendation.classification === 'COMP'
              ? '竞争课程'
              : recommendation.classification === 'OUT'
                ? '未进入推荐组合'
                : recommendation.classification === 'SELECTED' ? '已形成选课结果' : '模型状态';
          const participantNumber = participantCount == null ? null : Number(participantCount);
          const capacityNumber = merged.capacity == null ? null : Number(merged.capacity);
          const capacityDelta = participantNumber != null && capacityNumber != null
            ? participantNumber - capacityNumber : null;
          const capacityStatus = capacityDelta == null
            ? '人数待更新'
            : capacityDelta > 0
              ? `超容量 ${capacityDelta} 人`
              : capacityDelta === 0 ? '达到容量' : `尚余 ${Math.abs(capacityDelta)} 个容量`;
          return <div key={item.class_id} className={`jwxk-task-course-row${isWeight ? ' is-weight' : ''}`}>
            <span className="jwxk-task-course-name"><b>{item.course_name || item.course_code}</b><small>{item.teacher || '教师待定'} · {item.class_id}</small></span>
            <span className="jwxk-task-course-metric"><b>{participantCount ?? '-'}/{merged.capacity ?? '-'}</b><small>{participantLabel} / 容量</small></span>
            {isWeight && <span className="jwxk-task-course-metric"><b>{currentWeight ?? '未投'}{currentWeight != null ? ' 点' : ''}</b><small>当前投权</small></span>}
            {isWeight && <span className="jwxk-task-course-metric"><b>{recommendationText}</b><small>{classificationText}</small></span>}
            <Tag className="jwxk-task-course-status" color={capacityDelta == null ? 'default' : capacityDelta >= 0 ? 'warning' : 'success'}>{capacityStatus}</Tag>
          </div>;
        })}</div>
      </section>;
    })}</div>
        {isWeight && <Text type="secondary">最近计算 {formatTaskTimestamp(task.weight_status?.last_calculated_at)} · 最近调整 {task.weight_status?.last_adjusted_at ? formatTaskTimestamp(task.weight_status.last_adjusted_at) : '暂无'}</Text>}
        {(task.results || []).length > 0 && <div className="jwxk-task-history"><Text strong>最近操作</Text>{[...(task.results || [])].slice(-5).reverse().map((result, index) => <div key={`${result.at || index}:${result.class_id || ''}`}><span>{result.action === 'weight_drop' ? '撤回权重' : result.action === 'weight_add' ? `投放 ${result.weight || ''} 点权重` : result.action === 'drop' ? '自动退选' : '提交选课'} · {historyCourseLabel(result)}</span><small>{result.message || `官方代码 ${result.code || '-'}`} · {formatTaskTimestamp(result.at)}</small></div>)}</div>}
        <Space wrap><Button type={isWeight && attentionTaskId === task.task_id ? 'primary' : 'default'} className={isWeight && attentionTaskId === task.task_id ? 'jwxk-start-strategy-attention' : ''} icon={<PlayCircleOutlined />} loading={taskActionLoading === `${task.task_id}:start`} disabled={active || task.status === 'success'} onClick={() => runTaskAction(task, 'start')}>{isWeight ? '启动实时策略' : isSwap ? '开始追踪空位' : '同时启动全部方案组'}</Button>{isWeight && <Button type="primary" ghost icon={<ReloadOutlined />} loading={taskActionLoading === `${task.task_id}:check_now`} disabled={!active || task.status === 'success'} onClick={() => runTaskAction(task, 'check_now')}>立即检查并执行策略</Button>}<Button icon={<PauseCircleOutlined />} loading={taskActionLoading === `${task.task_id}:pause`} disabled={!active} onClick={() => runTaskAction(task, 'pause')}>暂停</Button><Button danger loading={taskActionLoading === `${task.task_id}:cancel`} onClick={() => Modal.confirm({ title: '取消并删除这个任务？', content: '任务会立即停止，并从任务列表中移除。', okText: '取消任务', okButtonProps: { danger: true }, onOk: () => runTaskAction(task, 'cancel') })}>取消任务</Button></Space>
      </Card>;
  })}{!tasks.length && <Empty description="尚未创建自动抢课或空位追踪任务" />}</div>;

  if (!batch && status) return <main className="course-selection-page"><Alert type="error" showIcon message="该轮次不存在或当前账号不可见" action={<Button onClick={() => navigate('/course-selection')}>返回批次</Button>} /></main>;

  return <main className="course-selection-page jwxk-workspace">
    <header className="jwxk-workspace-header"><Button className="jwxk-header-action jwxk-header-back" aria-label="返回选课轮次" icon={<ArrowLeftOutlined />} onClick={() => navigate('/course-selection')}>返回轮次</Button><div><Title level={3}>{batch?.name || '选课工作台'}</Title><Space wrap><Text type="secondary">{batch?.term_name} · {batch?.selection_type || '选课'} · 官方实时数据</Text>{batch?.allow_cross_campus && <Tag color="blue">允许跨校区选课</Tag>}</Space></div><Button className="jwxk-header-action jwxk-header-refresh" aria-label="刷新当前页面" icon={<ReloadOutlined />} onClick={() => view === 'catalog' ? loadCatalog(page) : view === 'selected' ? loadSelected() : loadTasks()}>刷新</Button></header>
    <Alert type="info" showIcon message="提交后请在“已选结果”中确认最终状态。" />
    <section className="jwxk-live-schedule" ref={scheduleRef}>
      <div className="jwxk-live-schedule__head">
        <div><Title level={4}>选课课表</Title><Text type="secondary">紧凑显示当前课表与待选方案；空闲节次可直接反查可选课程，也可切换班级、教师和教室课表比较。</Text></div>
        {catalogPreviewClasses.length > 0 && <Space wrap>{catalogPreviewClasses.map(course => (
          <Button key={course.class_id} size="small" onClick={() => cancelCatalogPreviewFromSchedule(course)}>
            取消“{course.course_name}”的课表预览
          </Button>
        ))}</Space>}
      </div>
      {termCode && <TimetablePage
        embedded
        preferredTermCode={termCode}
        initialViewMode="term"
        overlayCourses={allScheduleOverlay}
        presentation="selection"
        externalConflictMap={overlayConflictMap}
        onSlotSelect={handleSlotSelect}
        onPersonalCoursesChange={courses => {
          setPersonalCourses(courses);
          setPersonalScheduleReady(true);
        }}
      />}
    </section>
    <Segmented block value={view} onChange={setView} options={[{ label: '选课目录', value: 'catalog' }, { label: `我的方案 ${plan.length}`, value: 'plan' }, { label: '已选结果', value: 'selected' }, { label: '自动任务', value: 'tasks' }]} />
    {view === 'catalog' ? catalog : view === 'plan' ? planView : view === 'selected' ? selectedView : taskView}
    <Modal
      title="课程筛选"
      open={filterOpen}
      onCancel={() => setFilterOpen(false)}
      okText="应用筛选"
      cancelText="取消"
      onOk={() => { setCatalogFilters(cleanCatalogFilters(filterDraft)); setFilterOpen(false); }}
      footer={(_, { OkBtn, CancelBtn }) => <><Button onClick={() => setFilterDraft({ ...EMPTY_CATALOG_FILTERS })}>重置</Button><CancelBtn /><OkBtn /></>}
    >
      <div className="jwxk-filter-grid">
        <label><span>课程性质</span><Select allowClear value={filterDraft.courseNature || undefined} onChange={value => setFilterDraft(previous => ({ ...previous, courseNature: value || '' }))} options={effectiveFilterOptions.course_natures} placeholder={filterLoading ? '正在加载' : '全部性质'} /></label>
        <label><span>课程类别</span><Select allowClear value={filterDraft.courseCategory || undefined} onChange={value => setFilterDraft(previous => ({ ...previous, courseCategory: value || '' }))} options={effectiveFilterOptions.course_categories} placeholder={filterLoading ? '正在加载' : '全部类别'} /></label>
        <label><span>通识选修课类别</span><Select showSearch allowClear optionFilterProp="label" value={filterDraft.generalElectiveCategory || undefined} onChange={value => setFilterDraft(previous => ({ ...previous, generalElectiveCategory: value || '' }))} options={effectiveFilterOptions.general_elective_categories} placeholder={filterLoading ? '正在加载' : '全部通识类别'} /></label>
        <label><span>校区</span><Select allowClear value={filterDraft.campus || undefined} onChange={value => setFilterDraft(previous => ({ ...previous, campus: value || '' }))} options={effectiveFilterOptions.campuses} placeholder={filterLoading ? '正在加载' : '全部校区'} /></label>
        <label><span>开课单位</span><Select showSearch allowClear optionFilterProp="label" value={filterDraft.department || undefined} onChange={value => setFilterDraft(previous => ({ ...previous, department: value || '' }))} options={effectiveFilterOptions.departments} placeholder={filterLoading ? '正在加载' : '全部单位'} /></label>
        <label><span>开始节次</span><Select allowClear value={filterDraft.startSection || undefined} onChange={value => setFilterDraft(previous => ({ ...previous, startSection: value || '' }))} options={effectiveFilterOptions.sections} placeholder="不限" /></label>
        <label><span>结束节次</span><Select allowClear value={filterDraft.endSection || undefined} onChange={value => setFilterDraft(previous => ({ ...previous, endSection: value || '' }))} options={effectiveFilterOptions.sections} placeholder="不限" /></label>
      </div>
    </Modal>
    <Modal title="加入方案组" open={!!planAssignment} onCancel={() => setPlanAssignment(null)} onOk={confirmPlanAssignment} okText="加入方案组" okButtonProps={{ disabled: assignmentGroupId === '__new__' && !newGroupName.trim() }}>
      <div className="jwxk-group-form">
        <Text strong>{planAssignment?.course?.course_name} · {planAssignment?.course?.teacher || '教师待定'}</Text>
        <label><span>目标方案组</span><Select value={assignmentGroupId} onChange={setAssignmentGroupId} options={[...planGroupConfigs.map(group => ({ value: group.group_id, label: `${group.name}（目标 ${group.target_count} 门）` })), { value: '__new__', label: '新建方案组' }]} /></label>
        {assignmentGroupId === '__new__' && <><label><span>方案组名称</span><Input value={newGroupName} onChange={event => setNewGroupName(event.target.value)} placeholder="例如：A 类课程" maxLength={60} /></label><label><span>需要选中</span><InputNumber min={1} max={20} value={newGroupTargetCount} onChange={value => setNewGroupTargetCount(value || 1)} addonAfter="门" /></label></>}
      </div>
    </Modal>
    <Modal title={groupEditor?.group_id ? '编辑方案组' : '新建方案组'} open={!!groupEditor} onCancel={() => setGroupEditor(null)} onOk={saveGroupEditor} okText="保存" okButtonProps={{ disabled: !groupEditor?.name?.trim() }}>
      <div className="jwxk-group-form">
        <label><span>方案组名称</span><Input value={groupEditor?.name || ''} onChange={event => setGroupEditor(previous => ({ ...previous, name: event.target.value }))} placeholder="例如：A 类课程" maxLength={60} /></label>
        <label><span>需要选中</span><InputNumber min={1} max={20} value={groupEditor?.target_count || 1} onChange={value => setGroupEditor(previous => ({ ...previous, target_count: value || 1 }))} addonAfter="门" /></label>
        <Text type="secondary">组内候选按优先级尝试；高优先级课程满员、受限或冲突时自动顺延，达到目标门数后该组停止。</Text>
      </div>
    </Modal>
    <Modal
      title={`追踪空位并换课${vacancySwapTarget ? ` · ${vacancySwapTarget.course_name}` : ''}`}
      open={!!vacancySwapTarget}
      onCancel={() => setVacancySwapTarget(null)}
      onOk={createVacancySwapTask}
      okText="创建追踪组"
      okButtonProps={{ disabled: !vacancyDropIds.length }}
      width={680}
    >
      <div className="jwxk-group-form">
        <Alert type="warning" showIcon message="检测到意向教学班出现空位后，系统会先提交所选课程的退选，再等待官方确认；确认退选后才提交意向课程。期间名额仍可能被其他人抢走，任何结果不明确都会停止并要求人工核验。" />
        <label><span>意向教学班</span><Input value={`${vacancySwapTarget?.course_name || ''} · ${vacancySwapTarget?.teacher || '教师待定'} · ${vacancySwapTarget?.class_number || vacancySwapTarget?.class_id || ''}`} disabled /></label>
        <label><span>出现空位后自动退选</span><Select mode="multiple" value={vacancyDropIds} onChange={setVacancyDropIds} placeholder="选择需要先退掉的已选课程" options={selected.filter(item => item.course_code !== vacancySwapTarget?.course_code).map(item => ({ value: item.class_id, label: `${item.course_name} · ${item.teacher || '教师待定'}` }))} /></label>
        <Text type="secondary">可以为不同意向课程分别创建多个空位追踪组，它们会作为独立任务同时运行。</Text>
      </div>
    </Modal>
    <Modal title="为所选方案组配置策略投权" open={weightSetupOpen} onCancel={() => setWeightSetupOpen(false)} footer={<><Button onClick={() => setWeightSetupOpen(false)}>取消</Button><Button loading={weightBuilding} disabled={!taskGroupIds.length} onClick={buildWeightPlan}>生成所选组建议</Button><Button type="primary" icon={<RobotOutlined />} disabled={!taskGroupIds.length} onClick={createWeightStrategyTask}>为所选方案组创建策略投权</Button></>} width={620}>
      <div className="jwxk-group-form">
        <Alert type="info" showIcon message="选择本次策略需要管理的方案组" description="打开时默认全选。任务创建后会绑定这些方案组；组内候选、目标门数或优先级发生变化时，任务会自动同步。未选中的方案组不会参与本次计算，也不会被任务调整权重。" />
        <label><span>参与策略的方案组</span><Checkbox.Group className="jwxk-weight-group-picker" value={taskGroupIds} onChange={setTaskGroupIds} options={planGroupConfigs.map(group => ({ value: group.group_id, label: `${group.name}（目标 ${group.target_count} 门，${plan.filter(item => item.plan_group_id === group.group_id).length} 个候选）` }))} /></label>
        <label><span>年级人数</span><InputNumber min={1} max={100000} value={gradeSizeDraft} onChange={setGradeSizeDraft} placeholder="用于估计当前轮次整体竞争热度" /></label>
        <Text type="secondary">模型只在所选方案组内满足目标并分配权重。每门课程的 1–10 分意愿值在方案组列表中修改；教学班优先级只决定同一课程优先使用哪个教学班。</Text>
      </div>
    </Modal>
    <Modal title="策略投权建议" open={!!weightPlan} onCancel={() => setWeightPlan(null)} onOk={applyWeights} okText="确认并逐项提交" width={760}>{weightPlan && <><Alert type="info" showIcon message={`本次可重分配 ${weightPlan.budget} 点，推荐使用 ${weightPlan.used} 点`} description={`官方当前剩余 ${weightPlan.official_remaining ?? weightPlan.budget} 点，可撤回并重分配 ${weightPlan.reclaimable_weight || 0} 点。最低投放 ${weightPlan.minimum}，步长 ${weightPlan.step}。已有投权课程会先撤回、确认后再按建议值重新投放。概率为未校准的模型代理值，不代表真实录取概率。${weightPlan.approximate ? ' 当前为限时搜索得到的最佳可行解。' : ''}`} />{(weightPlan.warnings || []).map(value => <Alert key={value} type="warning" showIcon message={value} />)}<div className="jwxk-weight-groups">{(weightPlan.groups || []).map(group => <Tag key={group.group_id} color={group.satisfied ? 'success' : 'warning'}>{group.name} {group.selected_count}/{group.target_count}</Tag>)}</div>{(weightPlan.courses || []).map(item => <div className="jwxk-weight-row" key={item.class_id || item.course_id}><span><b>{item.course_name || item.name}</b><small>{item.teacher || '教师待定'} · 意愿 {item.utility} · {item.classification}</small><small>{item.current_participant_label || '已投注人数'} {item.current_participant_count ?? item.weight_participant_count ?? '-'} / 容量 {item.current_capacity ?? item.capacity ?? '-'}</small>{item.reapply_required && <small>当前已投 {item.current_weight ?? '-'} 点，提交时将先撤回再重投</small>}<small>保守 {(Number(item.scenario_success_rates?.conservative || 0) * 100).toFixed(1)}% · 中性 {(Number(item.scenario_success_rates?.neutral || 0) * 100).toFixed(1)}% · 激进 {(Number(item.scenario_success_rates?.aggressive || 0) * 100).toFixed(1)}%</small></span>{item.weight > 0 && !item.already_selected ? <InputNumber min={weightPlan.minimum} step={weightPlan.step} max={weightPlan.budget} value={item.weight} onChange={value => setWeightPlan(previous => { const items = previous.items.map(row => row.class_id === item.class_id ? { ...row, weight: value || previous.minimum } : row); return { ...previous, items, used: items.reduce((sum, row) => sum + Number(row.weight || 0), 0) }; })} /> : <Tag>{item.already_selected ? '已形成最终选课结果' : '本次不投'}</Tag>}</div>)}</>}</Modal>
    <CourseOutlineDrawer
      open={Boolean(outlineCourse)}
      course={outlineCourse}
      onClose={() => setOutlineCourse(null)}
    />
  </main>;
};

export default CourseSelectionWorkspacePage;
