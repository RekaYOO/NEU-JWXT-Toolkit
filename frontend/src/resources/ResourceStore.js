import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import {
  getCacheEvents,
  getCacheRefreshJob,
  getCachedAcademicReport,
  getCachedScores,
  getOfflineAcademicReport,
  getOfflineFestivalActivities,
  getOfflineResearchTraining,
  getOfflineScores,
  getResearchTrainingCache,
  getFestivalActivitiesCache,
  requestCacheRefresh,
} from '../services/api';

const ResourceContext = createContext(null);
const JOB_POLL_MS = 900;
const EVENT_POLL_MS = 15000;
const TERMINAL_JOB_STATES = new Set(['completed', 'failed', 'cancelled']);
const ACTIVE_SYNC_STATES = new Set(['starting', 'queued', 'running']);

export const deriveCachedResourceLoading = ({
  enabled = true, displayedData = null, state = {},
}) => Boolean(
  enabled
  && !displayedData
  && (state.loading !== false || ACTIVE_SYNC_STATES.has(state.syncState)),
);

const definitions = {
  scores: {
    online: getCachedScores,
    offline: getOfflineScores,
    offlineReadable: true,
  },
  'academic-report': {
    online: getCachedAcademicReport,
    offline: getOfflineAcademicReport,
    offlineReadable: true,
  },
  'research-training': {
    online: getResearchTrainingCache,
    offline: getOfflineResearchTraining,
    offlineReadable: true,
  },
  'festival-activities': {
    online: getFestivalActivitiesCache,
    offline: getOfflineFestivalActivities,
    offlineReadable: true,
  },
};

const metadataOf = (payload) => {
  const cache = payload?.cache || {};
  return {
    revision: cache.revision || payload?.revision || '',
    savedAt: cache.saved_at || payload?.saved_at || payload?.last_update || null,
    lastCheckedAt: cache.last_checked_at || payload?.last_checked_at || null,
    lastAttemptAt: cache.last_attempt_at || payload?.last_attempt_at || null,
    isStale: cache.is_stale ?? payload?.is_stale ?? payload?.is_fresh === false,
    dependencyRevisions: cache.dependency_revisions || {},
  };
};

const normalizeEventList = (response) => (
  Array.isArray(response) ? response : response?.events || []
);

const wait = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds));

export const ResourceProvider = ({
  children, offlineMode = false, identity = '',
}) => {
  const [states, setStates] = useState({});
  const statesRef = useRef(states);
  const generationRef = useRef(0);
  const eventCursorRef = useRef('');
  const refreshPromisesRef = useRef(new Map());

  useEffect(() => {
    statesRef.current = states;
  }, [states]);

  useEffect(() => {
    generationRef.current += 1;
    eventCursorRef.current = '';
    refreshPromisesRef.current.clear();
    setStates({});
  }, [identity, offlineMode]);

  const mergeState = useCallback((resource, patch) => {
    setStates(previous => ({
      ...previous,
      [resource]: { ...(previous[resource] || {}), ...patch },
    }));
  }, []);

  const publish = useCallback((resource, payload) => {
    const meta = metadataOf(payload);
    mergeState(resource, {
      availableData: payload,
      availableRevision: meta.revision,
      metadata: meta,
      loading: false,
      error: null,
    });
  }, [mergeState]);

  const clear = useCallback((resource) => {
    setStates(previous => {
      const next = { ...previous };
      delete next[resource];
      return next;
    });
  }, []);

  const load = useCallback(async (resource, { quiet = false } = {}) => {
    const definition = definitions[resource];
    if (!definition) throw new Error(`未注册的缓存资源: ${resource}`);
    if (offlineMode && !definition.offlineReadable) {
      throw new Error(`离线模式不支持资源: ${resource}`);
    }

    const generation = generationRef.current;
    const loader = offlineMode ? definition.offline : definition.online;
    if (!loader) throw new Error(`资源没有可用的读取器: ${resource}`);
    if (!quiet) mergeState(resource, { loading: true, error: null });

    try {
      const payload = await loader();
      if (generation !== generationRef.current) return null;
      if (payload?.available === false) {
        setStates(previous => ({
          ...previous,
          [resource]: {
            loading: false,
            error: null,
            cacheMissing: true,
          },
        }));
        return null;
      }
      const meta = metadataOf(payload);
      mergeState(resource, {
        availableData: payload,
        availableRevision: meta.revision,
        metadata: meta,
        loading: false,
        error: null,
      });
      return payload;
    } catch (error) {
      if (generation !== generationRef.current) return null;
      const missing = error.response?.status === 404;
      mergeState(resource, {
        loading: false,
        error: missing ? null : error,
        cacheMissing: missing,
      });
      if (!missing && !quiet) throw error;
      return null;
    }
  }, [mergeState, offlineMode]);

  const pollJob = useCallback(async (resource, jobId, generation) => {
    let job;
    do {
      await wait(JOB_POLL_MS);
      if (generation !== generationRef.current) return null;
      job = await getCacheRefreshJob(jobId);
      mergeState(resource, { syncState: job.status, job });
    } while (!TERMINAL_JOB_STATES.has(job.status));

    if (job.status === 'completed') {
      await load(resource, { quiet: true });
      return job;
    }
    const detail = job.error || job.error_kind || (
      job.status === 'cancelled' ? '后台同步已取消' : '后台同步失败'
    );
    mergeState(resource, {
      syncState: job.status,
      syncError: detail,
      loading: false,
    });
    throw new Error(detail);
  }, [load, mergeState]);

  const refresh = useCallback(async (
    resource, { force = false, reason = 'page_swr' } = {},
  ) => {
    if (offlineMode) return { status: 'offline' };
    const existing = refreshPromisesRef.current.get(resource);
    if (existing) return existing;

    const generation = generationRef.current;
    const promise = (async () => {
      mergeState(resource, { syncState: 'starting', syncError: null });
      const result = await requestCacheRefresh(resource, { force, reason });
      if (generation !== generationRef.current) return result;

      mergeState(resource, { syncState: result.status, job: result });
      if (result.status === 'fresh') {
        await load(resource, { quiet: true });
        return result;
      }
      const jobId = result.job_id || result.id;
      if (!jobId) {
        // Transitional compatibility: a refresh endpoint may return the payload.
        if (result.cache || result.scores || result.categories || result.topics || result.activities) {
          const meta = metadataOf(result);
          mergeState(resource, {
            availableData: result,
            availableRevision: meta.revision,
            metadata: meta,
            syncState: 'completed',
          });
          return result;
        }
        const detail = result.error || result.error_kind || (
          result.status === 'throttled'
            ? '最近一次后台同步失败，系统将在一分钟后自动重试；也可手动刷新'
            : '后台同步未能启动'
        );
        mergeState(resource, {
          syncState: result.status || 'failed',
          syncError: detail,
          loading: false,
        });
        throw new Error(detail);
      }
      return pollJob(resource, jobId, generation);
    })()
      .catch(error => {
        if (generation === generationRef.current) {
          mergeState(resource, {
            syncState: 'failed',
            syncError: error.response?.data?.detail || error.message,
          });
        }
        throw error;
      })
      .finally(() => {
        if (refreshPromisesRef.current.get(resource) === promise) {
          refreshPromisesRef.current.delete(resource);
        }
      });

    refreshPromisesRef.current.set(resource, promise);
    return promise;
  }, [load, mergeState, offlineMode, pollJob]);

  useEffect(() => {
    if (offlineMode || !identity) return undefined;
    let active = true;

    const checkEvents = async () => {
      try {
        const response = await getCacheEvents(eventCursorRef.current);
        if (!active) return;
        const events = normalizeEventList(response);
        const nextCursor = response?.cursor || response?.next_cursor;
        if (nextCursor !== undefined && nextCursor !== null) {
          eventCursorRef.current = String(nextCursor);
        }
        const resources = new Set(events
          .map(event => event.resource || event.key?.resource)
          .filter(Boolean));
        events.forEach(event => {
          window.dispatchEvent(new CustomEvent('neu-cache-event', {
            detail: event,
          }));
        });
        await Promise.all([...resources]
          .filter(resource => definitions[resource])
          .map(resource => load(resource, { quiet: true })));
      } catch (error) {
        // Event polling is advisory. Cached views must remain usable.
      }
    };

    const timer = setInterval(checkEvents, EVENT_POLL_MS);
    checkEvents();
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [identity, load, offlineMode]);

  const value = useMemo(() => ({
    states, load, publish, refresh, clear, offlineMode,
  }), [states, load, publish, refresh, clear, offlineMode]);

  return (
    <ResourceContext.Provider value={value}>
      {children}
    </ResourceContext.Provider>
  );
};

export const useCachedResource = (resource, { autoRefresh = true, enabled = true } = {}) => {
  const store = useContext(ResourceContext);
  if (!store) throw new Error('useCachedResource 必须在 ResourceProvider 内使用');
  const state = store.states[resource] || {};
  const [displayedData, setDisplayedData] = useState(
    () => enabled ? state.availableData || null : null,
  );
  const [displayedRevision, setDisplayedRevision] = useState(
    () => enabled ? state.availableRevision || '' : '',
  );
  const mountedRef = useRef(false);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  useEffect(() => {
    if (!enabled) return undefined;
    mountedRef.current = true;
    const cachedInMemory = store.states[resource]?.availableData || null;
    if (cachedInMemory) {
      const meta = metadataOf(cachedInMemory);
      setDisplayedData(cachedInMemory);
      setDisplayedRevision(meta.revision);
      if (autoRefresh && !store.offlineMode && meta.isStale) {
        store.refresh(resource).catch(() => {});
      }
    }
    store.load(resource, { quiet: Boolean(cachedInMemory) }).then(payload => {
      if (!mountedRef.current) return;
      if (!payload) {
        if (autoRefresh && !store.offlineMode) {
          store.refresh(resource).catch(() => {});
        }
        return;
      }
      const meta = metadataOf(payload);
      if (!cachedInMemory) {
        setDisplayedData(payload);
        setDisplayedRevision(meta.revision);
      }
      if (autoRefresh && !store.offlineMode && (meta.isStale || !meta.revision)) {
        store.refresh(resource).catch(() => {});
      }
    }).catch(() => {});
    return () => {
      mountedRef.current = false;
    };
  }, [autoRefresh, enabled, resource, store.load, store.offlineMode, store.refresh]);

  useEffect(() => {
    if (!enabled || !state.availableData || displayedData) return;
    setDisplayedData(state.availableData);
    setDisplayedRevision(state.availableRevision || '');
  }, [displayedData, enabled, state.availableData, state.availableRevision]);

  useEffect(() => {
    if (!enabled) return undefined;
    const onFocus = () => {
      store.load(resource, { quiet: true }).then(payload => {
        const meta = metadataOf(payload);
        if (
          autoRefresh
          && !store.offlineMode
          && (!payload || meta.isStale)
        ) {
          store.refresh(resource).catch(() => {});
        }
      }).catch(() => {});
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') onFocus();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [autoRefresh, enabled, resource, store.load, store.offlineMode, store.refresh]);

  const applyData = useCallback((payload) => {
    if (!payload || !enabledRef.current) return;
    const meta = metadataOf(payload);
    setDisplayedData(payload);
    setDisplayedRevision(meta.revision);
  }, []);

  const applyAvailable = useCallback(() => {
    applyData(state.availableData);
  }, [applyData, state.availableData]);

  const refresh = useCallback((options = {}) => (
    store.refresh(resource, { force: true, reason: 'manual', ...options })
  ), [resource, store.refresh]);

  const reloadAndApply = useCallback(async () => {
    const payload = await store.load(resource, { quiet: true });
    if (payload && enabledRef.current) {
      const meta = metadataOf(payload);
      setDisplayedData(payload);
      setDisplayedRevision(meta.revision);
    }
    return payload;
  }, [resource, store.load]);

  const updateData = useCallback((nextOrUpdater) => {
    const next = typeof nextOrUpdater === 'function'
      ? nextOrUpdater(displayedData)
      : nextOrUpdater;
    if (!next) return;
    const meta = metadataOf(next);
    setDisplayedData(next);
    setDisplayedRevision(meta.revision);
    store.publish(resource, next);
  }, [displayedData, resource, store.publish]);

  const clear = useCallback(() => {
    setDisplayedData(null);
    setDisplayedRevision('');
    store.clear(resource);
  }, [resource, store.clear]);

  return {
    data: displayedData,
    displayedRevision,
    availableData: state.availableData || null,
    availableRevision: state.availableRevision || '',
    updateAvailable: Boolean(
      displayedData
      && state.availableRevision
      && displayedRevision
      && state.availableRevision !== displayedRevision
    ),
    metadata: state.metadata || {},
    loading: deriveCachedResourceLoading({ enabled, displayedData, state }),
    error: state.error || null,
    syncState: state.syncState || 'idle',
    syncError: state.syncError || null,
    refresh,
    applyAvailable,
    applyData,
    reloadAndApply,
    updateData,
    clear,
    reloadCache: () => store.load(resource, { quiet: true }),
  };
};
