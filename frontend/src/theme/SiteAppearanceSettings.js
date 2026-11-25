import React, { useEffect, useState } from 'react';
import { Alert, Button, Radio, Spin } from 'antd';
import { getSiteAppearance, saveSiteAppearance } from '../services/api';
import { builtinThemes } from './themeContract';

export default function SiteAppearanceSettings({ onSaved, onUnauthorized }) {
  const [current, setCurrent] = useState('');
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState('');
  const handleError = (failure, fallback) => {
    if (failure.response?.status === 401) onUnauthorized?.(failure);
    setError(fallback);
  };
  const load = async () => {
    setLoading(true);
    setNotice('');
    try {
      const result = await getSiteAppearance();
      setCurrent(result.site_default); setSelected(result.site_default); setError('');
    } catch (failure) { handleError(failure, '站点主题读取失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);
  return <section className="admin-panel admin-appearance" aria-labelledby="admin-theme-heading">
    <div className="admin-panel-heading">
      <div><h2 id="admin-theme-heading">站点默认主题</h2><p>应用于登录页和未设置个人主题的账号。</p></div>
    </div>
    {error && <Alert type="error" showIcon message={error} />}
    {error && <Button disabled={busy || loading} onClick={load}>重新读取</Button>}
    {notice && <Alert type="success" showIcon message={notice} />}
    {loading && <div className="admin-loading" role="status"><Spin /><span>正在读取站点主题</span></div>}
    {current && <Radio.Group className="admin-theme-choices" aria-label="站点默认主题" value={selected} disabled={busy || loading} onChange={event => { setSelected(event.target.value); setNotice(''); }}>
      {builtinThemes.map(item => <Radio value={item.id} key={item.id}>
        <span className="admin-theme-option">
          <strong>{item.name}</strong>
          <span className="admin-theme-swatches" aria-hidden="true">
            {[item.colors.primary, item.colors.canvas, item.colors.surface].map((color, index) => <span key={index} style={{ background: color }} />)}
          </span>
          {current === item.id && <small>当前默认</small>}
        </span>
      </Radio>)}
    </Radio.Group>}
    {current && <Button type="primary" disabled={loading || selected === current} loading={busy} onClick={async () => {
        setBusy(true);
        setNotice('');
        try {
          const result = await saveSiteAppearance({ previous: current, site_default: selected });
          setCurrent(result.site_default); setSelected(result.site_default); setError('');
          setNotice('已更新站点默认主题');
          onSaved?.(result.site_default);
        } catch (failure) { handleError(failure, failure.response?.status === 409 ? '站点默认主题已被其他管理员修改，请重新读取' : '站点主题保存失败'); }
        finally { setBusy(false); }
      }}>保存站点默认主题</Button>}
  </section>;
}
