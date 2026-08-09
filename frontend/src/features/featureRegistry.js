import React from 'react';
import {
  BellOutlined,
  BookOutlined,
  CalendarOutlined,
  ExperimentOutlined,
  ExportOutlined,
  FileTextOutlined,
  ReadOutlined,
  ScheduleOutlined,
  StarOutlined,
} from '@ant-design/icons';
import { hasOfflineExportData } from '../export/exportTools';

// Stable capability metadata shared by routing and navigation. Route elements
// remain in App.js so loading and error boundaries can evolve independently.
export const FEATURE_REGISTRY = Object.freeze([
  { id: 'scores', path: '/scores', label: '成绩明细', icon: <BookOutlined />, offline: 'has_scores' },
  { id: 'grade-tracking', path: '/grade-tracking', label: '成绩追踪', icon: <BellOutlined /> },
  { id: 'academic-report', path: '/academic-report', label: '培养计划', icon: <ScheduleOutlined />, offline: 'has_report' },
  { id: 'timetable', path: '/timetable', label: '查询课表', icon: <CalendarOutlined /> },
  { id: 'experiment-courses', path: '/experiment-courses', label: '实验选课', icon: <ExperimentOutlined /> },
  { id: 'research-training', path: '/research-training', label: '科研训练', icon: <ReadOutlined />, offline: 'has_research' },
  { id: 'evaluation', path: '/evaluation', label: '自动评教', icon: <StarOutlined /> },
  { id: 'exams', path: '/exams', label: '我的考试', icon: <CalendarOutlined /> },
  { id: 'export', path: '/export', label: '导出下载', icon: <ExportOutlined />, offline: 'export' },
  { id: 'logs', path: '/logs', label: '系统日志', icon: <FileTextOutlined /> },
]);

const FEATURES_BY_ID = Object.freeze(Object.fromEntries(
  FEATURE_REGISTRY.map(feature => [feature.id, feature]),
));

export const featureAvailable = (id, { offlineMode = false, offlineCapabilities = {} } = {}) => {
  const feature = FEATURES_BY_ID[id];
  if (!feature) return false;
  if (!offlineMode) return true;
  if (feature.offline === 'export') return hasOfflineExportData(offlineCapabilities);
  return Boolean(feature.offline && offlineCapabilities[feature.offline]);
};

export const visibleMenuItems = ({ offlineMode = false, offlineCapabilities = {} } = {}) => (
  FEATURE_REGISTRY
    .filter(feature => featureAvailable(feature.id, { offlineMode, offlineCapabilities }))
    .map(({ path, label, icon }) => ({ key: path, label, icon }))
);

export const pageTitles = Object.freeze(Object.fromEntries(
  FEATURE_REGISTRY.map(({ path, label }) => [path, label]),
));

export const offlineDefaultPath = (offlineCapabilities = {}) => {
  const preferred = FEATURE_REGISTRY.find(feature => (
    feature.offline && featureAvailable(feature.id, {
      offlineMode: true,
      offlineCapabilities,
    })
  ));
  return preferred?.path || '/login';
};
