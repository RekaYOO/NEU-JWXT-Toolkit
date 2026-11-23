import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ConfigProvider } from 'antd';
import { getAccessStatus, getAppearance, saveAppearance } from '../services/api';
import { builtinThemes, themeTokens, themeVariables, validateTemplate } from './themeContract';
import './appearance.css';

const AppearanceContext = createContext(null);
export const useAppearance = () => useContext(AppearanceContext);
const initial = { revision: 0, selected: '', templates: [], site_default: 'blue', effective: 'blue' };

export default function ApplicationTheme({ identity = '', publicTheme, children }) {
  const [state, setState] = useState({ owner: '', value: initial, loaded: false });
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const [savingOwners, setSavingOwners] = useState(() => new Set());
  const saving = savingOwners.has(identity);
  const [reduced, setReduced] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false);
  const identityRef = useRef(identity);
  const generation = useRef(0);
  const saveLock = useRef(new Set());
  identityRef.current = identity;
  const owned = state.owner === identity;
  const value = owned ? state.value : initial;
  const loaded = owned && state.loaded;
  const all = [...builtinThemes, ...value.templates];
  const applied = all.find(item => item.id === (value.selected || value.site_default)) || builtinThemes[1];
  const template = preview?.owner === identity ? preview.template : applied;

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const change = () => setReduced(Boolean(media?.matches));
    media?.addEventListener?.('change', change);
    return () => media?.removeEventListener?.('change', change);
  }, []);

  const reload = useCallback(async () => {
    if (identityRef.current !== identity) return;
    const ticket = ++generation.current;
    setError('');
    try {
      if (!identity) {
        setState({ owner: '', value: initial, loaded: false });
        const candidate = publicTheme === undefined ? (await getAccessStatus()).site_default : publicTheme;
        const siteDefault = builtinThemes.some(item => item.id === candidate) ? candidate : 'blue';
        if (ticket === generation.current && identityRef.current === identity) {
          setState({ owner: '', value: { ...initial, site_default: siteDefault, effective: siteDefault }, loaded: false });
        }
        return;
      }
      const next = await getAppearance();
      next.templates.forEach(item => validateTemplate(item));
      if (ticket === generation.current && identityRef.current === identity) setState({ owner: identity, value: next, loaded: true });
    } catch (failure) {
      if (ticket === generation.current && identityRef.current === identity) setError(failure.message || '外观设置读取失败');
    }
  }, [identity, publicTheme]);

  useEffect(() => {
    setPreview(null);
    reload();
    return () => { generation.current += 1; };
  }, [reload]);

  useLayoutEffect(() => {
    const root = document.documentElement;
    const variables = themeVariables(template);
    const previous = Object.fromEntries(Object.keys(variables).map(key => [key, root.style.getPropertyValue(key)]));
    Object.entries(variables).forEach(([key, setting]) => root.style.setProperty(key, setting));
    root.dataset.themeMotion = reduced || template.motion === 'reduced' ? 'reduced' : 'full';
    return () => {
      Object.entries(previous).forEach(([key, setting]) => setting ? root.style.setProperty(key, setting) : root.style.removeProperty(key));
      delete root.dataset.themeMotion;
    };
  }, [template, reduced]);

  const persist = async next => {
    if (identityRef.current !== identity) throw new Error('账号已切换，请重新打开外观设置');
    if (!identity || !loaded || saveLock.current.has(identity)) throw new Error('外观设置尚未就绪或正在保存');
    next.templates.forEach(item => validateTemplate(item));
    const ticket = generation.current;
    saveLock.current.add(identity);
    setSavingOwners(new Set(saveLock.current));
    try {
      const result = await saveAppearance({ revision: value.revision, ...next });
      if (ticket === generation.current && identityRef.current === identity) {
        setState({ owner: identity, value: result, loaded: true });
        setPreview(null);
      }
      return result;
    } finally {
      saveLock.current.delete(identity);
      setSavingOwners(new Set(saveLock.current));
    }
  };

  return <AppearanceContext.Provider value={{ ...value, identity, all, applied, loaded, saving, error, reload, persist,
    preview: candidate => { if (identityRef.current !== identity) return; validateTemplate(candidate, { builtin: builtinThemes.some(item => item.id === candidate.id) }); setPreview({ owner: identity, template: candidate }); },
    cancelPreview: () => setPreview(current => current?.owner === identity ? null : current), previewing: preview?.owner === identity }}>
    <ConfigProvider theme={themeTokens(template, reduced)}>{children}</ConfigProvider>
  </AppearanceContext.Provider>;
}
