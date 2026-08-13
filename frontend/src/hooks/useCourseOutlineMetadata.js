import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getCourseOutlineMetadata,
  getCourseOutlineMetadataSyncStatus,
  startCourseOutlineMetadataSync,
} from '../services/api';

const STATUS_POLL_MS = 15000;
const codeOf = course => String(course?.course_code || course?.code || '').trim();

export default function useCourseOutlineMetadata({ courses = [], enabled, offlineMode = false }) {
  const [metadata, setMetadata] = useState({});
  const [syncing, setSyncing] = useState(false);
  const [status, setStatus] = useState(null);
  const uniqueCourses = useMemo(() => {
    const map = new Map();
    courses.forEach(course => { const code = codeOf(course); if (code && !map.has(code)) map.set(code, course); });
    return [...map.values()];
  }, [courses]);
  const codes = useMemo(() => uniqueCourses.map(codeOf), [uniqueCourses]);

  const reload = useCallback(async () => {
    if (offlineMode || !codes.length) return {};
    const response = await getCourseOutlineMetadata(codes);
    const incoming = Object.fromEntries((response.items || []).map(item => [item.course_code, item]));
    setMetadata(current => ({ ...current, ...incoming }));
    return incoming;
  }, [codes, offlineMode]);

  useEffect(() => {
    if (!enabled || offlineMode || !uniqueCourses.length) { setSyncing(false); return undefined; }
    let active = true;
    let submitted = false;
    let terminalReloaded = false;
    let taskRunning = false;
    let settled = false;
    let timer;
    let eventReloadTimer;
    const submitMissing = async () => {
      const cached = await reload().catch(() => ({}));
      const missing = uniqueCourses.filter(course => !cached[codeOf(course)]);
      if (!active || !missing.length) {
        if (active) setSyncing(false);
        settled = true;
        if (timer) window.clearInterval(timer);
        return;
      }
      submitted = true;
      const next = await startCourseOutlineMetadataSync(missing);
      taskRunning = Boolean(next?.running);
      if (active) { setStatus(next); setSyncing(taskRunning); }
    };
    const check = async () => {
      const next = await getCourseOutlineMetadataSyncStatus();
      if (!active) return;
      taskRunning = Boolean(next?.running);
      setStatus(next); setSyncing(taskRunning);
      if (!next?.running && !submitted) await submitMissing();
      if (!next?.running && submitted && !terminalReloaded) {
        terminalReloaded = true;
        await reload().catch(() => null);
        settled = true;
        if (timer) window.clearInterval(timer);
      }
    };
    setSyncing(true);
    check().catch(() => submitMissing().catch(() => { if (active) setSyncing(false); }));
    timer = window.setInterval(() => {
      if (!settled) check().catch(() => null);
    }, STATUS_POLL_MS);
    const onEvent = event => {
      if (event?.detail?.resource !== 'course-outline-metadata') return;
      // A batch sync commits one course at a time. Updating table rows for
      // every intermediate commit would recreate native filter menus and
      // discard the user's unconfirmed selection. Merge once at completion.
      if (taskRunning) return;
      const variant = String(event.detail.variant || '');
      const code = variant.startsWith('course:') ? variant.slice(7) : '';
      if (code && !codes.includes(code)) return;
      if (eventReloadTimer) window.clearTimeout(eventReloadTimer);
      eventReloadTimer = window.setTimeout(() => {
        eventReloadTimer = null;
        if (active) reload().catch(() => null);
      }, 100);
    };
    window.addEventListener('neu-cache-event', onEvent);
    return () => {
      active = false;
      window.clearInterval(timer);
      if (eventReloadTimer) window.clearTimeout(eventReloadTimer);
      window.removeEventListener('neu-cache-event', onEvent);
    };
  }, [codes, enabled, offlineMode, reload, uniqueCourses]);

  const retryFailed = useCallback(() => {
    const failed = new Set(status?.errors || []);
    const coursesToRetry = uniqueCourses.filter(course => failed.has(codeOf(course)));
    if (!coursesToRetry.length) return Promise.resolve(status);
    setSyncing(true);
    return startCourseOutlineMetadataSync(coursesToRetry, true).then(next => { setStatus(next); return next; });
  }, [status, uniqueCourses]);

  return { metadata, syncing, status, reload, retryFailed };
}
