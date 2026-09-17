const BUNDLE_IMPORTERS = Object.freeze({
  academic: () => import(/* webpackChunkName: "route-academic" */ '../routeBundleAcademic'),
  courseSelection: () => import(/* webpackChunkName: "route-course-selection" */ '../routeBundleCourseSelection'),
  export: () => import(/* webpackChunkName: "route-export" */ '../routeBundleExport'),
  experimentCourses: () => import(/* webpackChunkName: "route-experiment-courses" */ '../pages/ExperimentCoursePage'),
  evaluation: () => import(/* webpackChunkName: "route-evaluation" */ '../pages/EvaluationPage'),
  exams: () => import(/* webpackChunkName: "route-exams" */ '../pages/ExamPage'),
  researchTraining: () => import(/* webpackChunkName: "route-research-training" */ '../pages/ResearchTrainingPage'),
  courseOutlines: () => import(/* webpackChunkName: "route-course-outlines" */ '../pages/CourseOutlinePage'),
  systemSettings: () => import(/* webpackChunkName: "route-system-settings" */ '../pages/SystemSettingsPage'),
});

export const ASYNC_ROUTE_DEFINITIONS = Object.freeze({
  'grade-tracking': { bundle: 'academic', exportName: 'GradeTrackingPage', path: '/grade-tracking' },
  'academic-report': { bundle: 'academic', exportName: 'AcademicReportPage', path: '/academic-report' },
  'experiment-courses': { bundle: 'experimentCourses', exportName: 'default', path: '/experiment-courses' },
  'research-training': { bundle: 'researchTraining', exportName: 'default', path: '/research-training' },
  evaluation: { bundle: 'evaluation', exportName: 'default', path: '/evaluation' },
  exams: { bundle: 'exams', exportName: 'default', path: '/exams' },
  'course-selection': { bundle: 'courseSelection', exportName: 'CourseSelectionPage', path: '/course-selection' },
  'course-selection-workspace': { bundle: 'courseSelection', exportName: 'CourseSelectionWorkspacePage', path: '/course-selection/:batchCode' },
  'course-selection-archive': { bundle: 'courseSelection', exportName: 'CourseSelectionArchivePage', path: '/course-selection/archive/:archiveId' },
  'course-outlines': { bundle: 'courseOutlines', exportName: 'default', path: '/course-outlines' },
  'system-settings': { bundle: 'systemSettings', exportName: 'default', path: '/system-settings' },
  export: { bundle: 'export', exportName: 'ExportPage', path: '/export' },
  'festival-activities': { bundle: 'export', exportName: 'FestivalActivitiesPage', path: '/export/festival-activities' },
  'academic-documents': { bundle: 'export', exportName: 'AcademicDocumentsPage', path: '/export/academic-documents' },
});

export const ASYNC_ROUTE_IDS = Object.freeze(Object.keys(ASYNC_ROUTE_DEFINITIONS));

const createBundleState = importer => {
  let loaded = null;
  let pending = null;
  const load = ({ retry = false } = {}) => {
    if (retry) pending = null;
    if (loaded) return Promise.resolve(loaded);
    if (!pending) {
      pending = Promise.resolve().then(importer).then(module => {
        loaded = module;
        return module;
      }).catch(error => {
        pending = null;
        throw error;
      });
    }
    return pending;
  };
  return {
    load,
    peek: () => loaded,
    reset: () => { loaded = null; pending = null; },
  };
};

export const createRouteModuleRegistry = importers => Object.fromEntries(
  Object.entries(importers).map(([id, importer]) => [id, createBundleState(importer)]),
);

const bundles = createRouteModuleRegistry(BUNDLE_IMPORTERS);

const resolveComponent = (routeId, module) => {
  const definition = ASYNC_ROUTE_DEFINITIONS[routeId];
  if (!definition) throw new Error(`Unknown async route: ${routeId}`);
  const Component = module?.[definition.exportName];
  if (!Component) throw new Error(`Async route export missing: ${routeId}`);
  return Component;
};

export const routeIdForPath = pathname => {
  const normalized = String(pathname || '').split(/[?#]/, 1)[0].replace(/\/+$/, '') || '/';
  if (/^\/course-selection\/archive\/[^/]+/.test(normalized)) return 'course-selection-archive';
  if (/^\/course-selection\/[^/]+/.test(normalized)) return 'course-selection-workspace';
  if (normalized === '/course-selection') return 'course-selection';
  if (normalized === '/export/festival-activities') return 'festival-activities';
  if (normalized === '/export/academic-documents') return 'academic-documents';
  if (normalized === '/export') return 'export';
  return ASYNC_ROUTE_IDS.find(id => ASYNC_ROUTE_DEFINITIONS[id].path === normalized) || null;
};

export const loadRouteComponent = async (routeId, options) => {
  const definition = ASYNC_ROUTE_DEFINITIONS[routeId];
  if (!definition) throw new Error(`Unknown async route: ${routeId}`);
  return resolveComponent(routeId, await bundles[definition.bundle].load(options));
};

export const loadedRouteComponent = routeId => {
  const definition = ASYNC_ROUTE_DEFINITIONS[routeId];
  if (!definition) return null;
  const module = bundles[definition.bundle].peek();
  return module ? resolveComponent(routeId, module) : null;
};

export const preloadRoute = routeId => loadRouteComponent(routeId).catch(() => null);

export const preloadRoutePath = pathname => {
  const routeId = routeIdForPath(pathname);
  return routeId ? preloadRoute(routeId) : Promise.resolve(null);
};

export const retryRouteComponent = routeId => loadRouteComponent(routeId, { retry: true });

export const resetRouteModulesForTests = () => {
  Object.values(bundles).forEach(bundle => bundle.reset());
};
