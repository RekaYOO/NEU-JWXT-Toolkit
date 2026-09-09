import { useCallback, useEffect, useRef, useState } from 'react';
import { getFestivalServiceStatus, updateFestivalSettings } from '../services/api';

export default function useFestivalConnection(offline) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [savingState, setSavingState] = useState(false);
  const generation = useRef(0);
  const saving = useRef(false);
  const currentProbe = useRef(null);
  const check = useCallback(async () => {
    if (offline || saving.current) return null;
    if (currentProbe.current) return currentProbe.current;
    const current = ++generation.current;
    setBusy(true);
    const request = (async () => {
      try {
        const result = await getFestivalServiceStatus();
        if (current !== generation.current) return null;
        setStatus(result);
        return result;
      } catch (error) {
        if (current === generation.current) setStatus(previous => ({
          ...previous,
          service_authenticated: false,
          service_auth_state: 'service_unavailable',
          message: error.message || '创院系统状态核验失败，请重新检测',
        }));
        return null;
      } finally {
        if (current === generation.current) setBusy(false);
      }
    })();
    currentProbe.current = request;
    try { return await request; }
    finally { if (currentProbe.current === request) currentProbe.current = null; }
  }, [offline]);
  const change = useCallback(async mode => {
    if (offline || saving.current) return null;
    const current = ++generation.current;
    currentProbe.current = null;
    saving.current = true;
    setSavingState(true);
    setBusy(true);
    try {
      const result = await updateFestivalSettings(mode);
      if (current !== generation.current) return null;
      setStatus(result);
    } catch (error) {
      if (current === generation.current) setStatus(previous => ({
        ...previous, message: error.message || '创院线路保存失败，请重新检测',
        service_authenticated: false, service_auth_state: 'service_unavailable',
      }));
      return null;
    } finally {
      if (current === generation.current) {
        saving.current = false;
        setSavingState(false);
        setBusy(false);
      }
    }
    return check();
  }, [check, offline]);
  const block = useCallback(() => {
    generation.current += 1;
    currentProbe.current = null;
    setBusy(false);
    setStatus(previous => ({
      ...previous, service_authenticated: false, service_auth_state: 'campus_network_blocked',
      error_code: 'WEBVPN_CAMPUS_NETWORK_BLOCKED',
      message: '校园网环境下学校 WebVPN 不可用，请将创院线路切换为直连或跟随教务。',
    }));
  }, []);
  const unavailable = useCallback(() => {
    generation.current += 1;
    currentProbe.current = null;
    setBusy(false);
    setStatus(previous => ({
      ...previous, service_authenticated: false, service_auth_state: 'service_unavailable',
      error_code: 'CXCY_SERVICE_UNAVAILABLE',
      message: 'WebVPN 认证已完成，但创院系统暂时不可用；无需重复提交短信，已保存的活动仍可查看。',
    }));
  }, []);
  useEffect(() => {
    saving.current = false;
    setSavingState(false);
    setBusy(false);
    check();
    return () => { generation.current += 1; currentProbe.current = null; };
  }, [check]);
  return { status, busy, saving: savingState, check, change, block, unavailable };
}
