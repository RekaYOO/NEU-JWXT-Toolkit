import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  getCourseOutlineMetadata,
  getCourseOutlineMetadataSyncStatus,
  startCourseOutlineMetadataSync,
} from '../services/api';

const STATUS_POLL_MS = 5000;
const STALLED_SYNC_MS = 120000;
const codeOf = course => String(course?.course_code || course?.code || '').trim();
const EMPTY_COURSES = [];

export default function useCourseOutlineMetadata({ courses = EMPTY_COURSES, enabled, offlineMode = false }) {
  const [metadata, setMetadata] = useState({});
  const [syncing, setSyncing] = useState(false);
  const [status, setStatus] = useState(null);
  const metadataRef = useRef({});
  const generationRef = useRef(0);
  const retryRef = useRef(null);
  const uniqueCourses = useMemo(() => {
    const map = new Map();
    courses.forEach(course => { const code = codeOf(course); if (code && !map.has(code)) map.set(code, course); });
    return [...map.values()];
  }, [courses]);
  const codes = useMemo(() => uniqueCourses.map(codeOf), [uniqueCourses]);

  const reload = useCallback(async () => {
    if (offlineMode || !codes.length) return {};
    const generation = generationRef.current;
    const response = await getCourseOutlineMetadata(codes);
    if (generation !== generationRef.current) return {};
    const incoming = Object.fromEntries((response.items || [])
      .map(item => [codeOf(item), item])
      .filter(([code]) => codes.includes(code)));
    // A cache-only response is authoritative, including deleted/missing rows.
    metadataRef.current = incoming;
    setMetadata(incoming);
    return incoming;
  }, [codes, offlineMode]);

  useEffect(() => {
    if (!enabled || offlineMode || !uniqueCourses.length) { setSyncing(false); return undefined; }
    let active = true;
    let submitted = false;
    let cacheLoaded = false;
    let observedRunning = false;
    let taskRunning = false;
    let settled = false;
    let failures = 0;
    let checking = false;
    let forceCourses = null;
    let lastStatus = null;
    let progressKey = '';
    let progressAt = Date.now();
    let timer;
    const missingCourses = () => uniqueCourses.filter(course => {
      const item = metadataRef.current[codeOf(course)];
      return !item || item.needs_sync;
    });
    const stopPolling = () => {
      if (timer) window.clearTimeout(timer);
      timer = null;
    };
    const finish = nextStatus => {
      if (!active) return;
      settled = true;
      stopPolling();
      taskRunning = false;
      setSyncing(false);
      lastStatus = nextStatus;
      setStatus(nextStatus);
    };
    const schedule = (delay = STATUS_POLL_MS) => {
      if (!active || settled) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = null;
        check();
      }, delay);
    };
    const check = async () => {
      if (!active || checking) return;
      checking = true;
      try {
        if (!cacheLoaded) {
          await reload();
          if (!active) return;
          cacheLoaded = true;
        }
        // Cached values must not wait behind an unrelated account-wide batch.
        if (!submitted && !observedRunning && !forceCourses && !missingCourses().length) {
          finish({ running: false, total: codes.length, completed: codes.length, failed: 0, errors: [] });
          return;
        }
        const next = await getCourseOutlineMetadataSyncStatus();
        if (!active) return;
        lastStatus = next;
        taskRunning = Boolean(next?.running);
        setStatus(next);
        if (taskRunning) {
          observedRunning = true;
          const key = JSON.stringify([next.total, next.completed, next.failed, next.current_course]);
          if (key !== progressKey) {
            progressKey = key;
            progressAt = Date.now();
          } else if (Date.now() - progressAt >= STALLED_SYNC_MS) {
            throw new Error('大纲元数据同步长时间无进展，请重试');
          }
          setSyncing(missingCourses().length > 0 || Boolean(forceCourses));
          failures = 0;
          schedule();
          return;
        }
        if (observedRunning || submitted) {
          await reload();
          if (!active) return;
          observedRunning = false;
        }
        if (submitted) {
          finish(next);
          return;
        }
        const missing = forceCourses || missingCourses();
        if (!missing.length) {
          finish(next);
          return;
        }
        const started = await startCourseOutlineMetadataSync(missing, Boolean(forceCourses));
        if (!active) return;
        // Only successful submissions consume the one automatic attempt.
        submitted = started?.accepted !== false;
        if (submitted) forceCourses = null;
        lastStatus = started;
        taskRunning = Boolean(started?.running);
        observedRunning = taskRunning;
        progressKey = '';
        progressAt = Date.now();
        setStatus(started);
        setSyncing(true);
        failures = 0;
        schedule(taskRunning || !submitted ? STATUS_POLL_MS : 0);
      } catch (error) {
        if (!active) return;
        failures += 1;
        // Count the whole read/status/submit cycle, not just the status GET.
        if (failures >= 3) {
          const errors = [...new Set([
            ...(lastStatus?.errors || []),
            ...(forceCourses || missingCourses()).map(codeOf),
          ])];
          finish({
            ...(lastStatus || {}),
            running: false,
            failed: errors.length,
            errors,
            error: error?.response?.data?.detail || error?.message || '大纲元数据读取失败',
          });
          return;
        }
        schedule();
      } finally {
        checking = false;
      }
    };
    retryRef.current = async () => {
      if (!active || checking || !settled) return lastStatus;
      const failed = new Set(lastStatus?.errors || []);
      const retry = uniqueCourses.filter(course => failed.has(codeOf(course)));
      if (!retry.length && !lastStatus?.error) return lastStatus;
      forceCourses = retry.length ? retry : missingCourses();
      if (!forceCourses.length) forceCourses = null;
      submitted = false;
      cacheLoaded = false;
      observedRunning = false;
      settled = false;
      failures = 0;
      progressKey = '';
      setSyncing(true);
      await check();
      return lastStatus;
    };
    setSyncing(true);
    setStatus(null);
    check();
    const onEvent = event => {
      if (event?.detail?.resource !== 'course-outline-metadata') return;
      // The worker commits one course at a time. Wait for the terminal status
      // and read one bounded snapshot instead of issuing one request per
      // commit while a large plan is being synchronized.
      if (!settled || checking) return;
      const variant = String(event.detail.variant || '');
      const code = variant.startsWith('course:') ? variant.slice(7) : '';
      if (code && !codes.includes(code)) return;
      // A later consumer may retry or refresh after this monitor has settled.
      // Observe that batch without submitting another automatic retry.
      settled = false;
      submitted = true;
      failures = 0;
      progressKey = '';
      schedule(100);
    };
    window.addEventListener('neu-cache-event', onEvent);
    return () => {
      active = false;
      generationRef.current += 1;
      retryRef.current = null;
      stopPolling();
      window.removeEventListener('neu-cache-event', onEvent);
    };
  }, [codes, enabled, offlineMode, reload, uniqueCourses]);

  const retryFailed = useCallback(() => retryRef.current?.() || Promise.resolve(null), []);

  return { metadata, syncing, status, reload, retryFailed };
}
