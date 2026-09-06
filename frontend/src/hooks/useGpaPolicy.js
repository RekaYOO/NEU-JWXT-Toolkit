import { useCallback, useEffect, useRef, useState } from 'react';
import { getGpaPolicy, saveGpaPolicy } from '../services/api';
import { useResourceIdentity } from '../resources/ResourceStore';

export default function useGpaPolicy(offlineMode) {
  const identity = useResourceIdentity();
  const [policy, setPolicy] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const generation = useRef(0);
  const request = useRef(0);
  const reload = useCallback(async () => {
    const epoch = generation.current;
    const id = ++request.current;
    try {
      const next = await getGpaPolicy(offlineMode);
      if (epoch === generation.current && id === request.current) {
        setPolicy(next);
        setError('');
      }
    } catch {
      if (epoch === generation.current && id === request.current) setError('绩点计算设置读取失败');
    }
  }, [offlineMode]);

  useEffect(() => {
    setPolicy(null);
    setSaving(false);
    setError('');
    reload();
    let timer;
    const schedule = event => {
      if (event.type === 'neu-cache-event' && !['academic-report', 'scores', 'course-outline-metadata'].includes(event.detail?.resource)) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(reload, 300);
    };
    window.addEventListener('focus', schedule);
    window.addEventListener('neu-cache-event', schedule);
    window.addEventListener('neu-gpa-policy', schedule);
    const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('neu-gpa-policy') : null;
    if (channel) channel.onmessage = schedule;
    return () => {
      generation.current += 1;
      window.clearTimeout(timer);
      window.removeEventListener('focus', schedule);
      window.removeEventListener('neu-cache-event', schedule);
      window.removeEventListener('neu-gpa-policy', schedule);
      channel?.close();
    };
  }, [identity, reload]);

  const save = useCallback(async mode => {
    if (offlineMode) return;
    const epoch = generation.current;
    request.current += 1;
    setSaving(true);
    try {
      const next = await saveGpaPolicy(mode);
      if (epoch !== generation.current) return;
      request.current += 1;
      setPolicy(next);
      setError('');
      if (typeof BroadcastChannel === 'function') {
        const channel = new BroadcastChannel('neu-gpa-policy');
        channel.postMessage('updated');
        channel.close();
      }
    } finally {
      if (epoch === generation.current) setSaving(false);
    }
  }, [offlineMode]);
  return { policy, error, saving, save, reload };
}
