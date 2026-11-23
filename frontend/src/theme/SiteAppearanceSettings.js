import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Select, Space } from 'antd';
import { getSiteAppearance, saveSiteAppearance } from '../services/api';
import { builtinThemes } from './themeContract';

export default function SiteAppearanceSettings() {
  const [current, setCurrent] = useState('');
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const load = async () => {
    try {
      const result = await getSiteAppearance();
      setCurrent(result.site_default); setSelected(result.site_default); setError('');
    } catch { setError('站点主题读取失败'); }
  };
  useEffect(() => { load(); }, []);
  return <Card title="站点默认主题">
    <p>适用于尚未选择个人主题的账号；不会覆盖已有个人选择。</p>
    {error && <Alert type="error" message={error} action={<Button onClick={load}>重新读取</Button>} />}
    <Space wrap>
      <Select aria-label="站点默认主题" value={selected || undefined} style={{ minWidth: 160 }} disabled={!current || busy} onChange={setSelected} options={builtinThemes.map(item => ({ value: item.id, label: item.name }))} />
      <Button disabled={!current} loading={busy} onClick={async () => {
        setBusy(true);
        try {
          const result = await saveSiteAppearance({ previous: current, site_default: selected });
          setCurrent(result.site_default); setError('');
        } catch (failure) { setError(failure.response?.status === 409 ? '站点默认主题已被其他管理员修改，请重新读取' : '站点主题保存失败'); }
        finally { setBusy(false); }
      }}>保存站点默认主题</Button>
    </Space>
  </Card>;
}
