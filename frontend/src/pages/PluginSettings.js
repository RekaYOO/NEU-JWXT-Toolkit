import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Space } from 'antd';
import { getPlugins, savePlugins } from '../services/api';
import { useResourceIdentity } from '../resources/ResourceStore';

const sample = { format: 1, id: 'semester-end', name: '学期倒计时', date: '2027-01-15', action: 'countdown' };

export function validatePlugin(value) {
  if (!value || value.format !== 1 || value.action !== 'countdown' ||
      typeof value.id !== 'string' || !/^[a-z][a-z0-9-]{0,47}$/.test(value.id) ||
      typeof value.name !== 'string' || !value.name.trim() || value.name.length > 60 ||
      typeof value.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value.date) ||
      !Number.isFinite(Date.parse(value.date)) || new Date(value.date).toISOString().slice(0, 10) !== value.date ||
      Object.keys(value).some(key => !Object.hasOwn(sample, key))) throw new Error('请选择有效的第 1 版倒计时插件 JSON 文件');
  return value;
}

function Registry() {
  const [registry, setRegistry] = useState(null);
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    getPlugins().then(result => {
      if (!Array.isArray(result.data)) throw new Error('插件清单格式错误');
      result.data.forEach(entry => validatePlugin(entry.plugin));
      if (active) setRegistry(result);
    }).catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [attempt]);
  async function persist(entries) {
    setBusy(true);
    setError('');
    try { setRegistry(await savePlugins(registry.revision, entries)); setPending(null); }
    catch (reason) { setError(reason.response?.status === 409 ? '其他窗口已修改插件，请重新加载' : '插件保存失败，请重新加载后重试'); }
    finally { setBusy(false); }
  }
  async function selectFile(event) {
    const file = event.target.files[0];
    event.target.value = '';
    setPending(null);
    if (!file) return;
    try {
      if (file.size > 8192) throw new Error('插件文件不能超过 8 KB');
      setPending(validatePlugin(JSON.parse(await file.text())));
      setError('');
    } catch (reason) { setError(reason.message); }
  }
  const entries = registry?.data || [];
  const today = new Date();
  const dayStart = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return <Space direction="vertical" style={{ width: '100%' }}>
    {error && <Alert type="error" message={error} action={<Button onClick={() => { setRegistry(null); setError(''); setAttempt(attempt + 1); }}>重新加载</Button>} />}
    <Card title="安装插件" loading={!registry && !error}><Space wrap>
      <label>导入插件文件 <input disabled={!registry || busy} type="file" accept=".json" onChange={selectFile} /></label>
      <Button disabled={!registry || busy} onClick={() => setPending(sample)}>试用学期倒计时</Button>
    </Space></Card>
    {pending && <Card title={pending.name}><p>允许使用本机日期计算距 {pending.date} 的天数。安装后对当前账号启用。</p><Space>
      <Button type="primary" disabled={!registry || busy || entries.some(entry => entry.plugin.id === pending.id)} onClick={() => persist([...entries, { plugin: pending, enabled: true }])}>{entries.some(entry => entry.plugin.id === pending.id) ? '已经安装' : '授权并安装'}</Button>
      <Button onClick={() => setPending(null)}>取消</Button></Space></Card>}
    {entries.map(({ plugin, enabled }) => <Card key={plugin.id} title={plugin.name}>
      <p>{enabled ? `${plugin.date} · 相距 ${Math.abs((Date.parse(plugin.date) - dayStart) / 86400000)} 天` : '已停用'}</p>
      <Space><Button disabled={busy} onClick={() => persist(entries.map(entry => entry.plugin.id === plugin.id ? { plugin, enabled: !enabled } : entry))}>{enabled ? '停用' : '启用'}</Button>
      <Button danger disabled={busy} onClick={() => persist(entries.filter(entry => entry.plugin.id !== plugin.id))}>卸载</Button></Space>
    </Card>)}
  </Space>;
}

export default function PluginSettings() {
  const identity = useResourceIdentity();
  return identity ? <Registry key={identity} /> : <Alert message="登录后可管理当前账号的插件" />;
}
