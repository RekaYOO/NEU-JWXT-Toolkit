import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card, Modal, Space, Tag, Typography, message } from 'antd';
import { CloudDownloadOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons';
import { getRuntimeUpdate, getRuntimeUpdateJob, startRuntimeUpdateDownload } from '../services/api';
import { checkNativeAppUpdate, installNativeAppUpdate, nativeShellInfo } from '../services/nativeBridge';

const { Text } = Typography;
const stateText = state => ({ downloading: '正在下载更新包', staged: '更新包已准备', requested: '已提交升级，服务将短暂重启', downloaded: '更新包已下载，请解压后重启程序', completed: '更新已完成', failed: '更新失败' }[state] || '尚未检查');
const errorText = reason => reason?.response?.data?.detail || reason?.message || '版本检查失败';

export default function VersionUpdateCard() {
  const shellInfo = nativeShellInfo();
  const isClient = shellInfo?.kind === 'client';
  const isLocal = shellInfo?.kind === 'local';
  const nativeApp = isClient || isLocal;
  const [snapshot, setSnapshot] = useState(null);
  const [serverSnapshot, setServerSnapshot] = useState(null);
  const [appSnapshot, setAppSnapshot] = useState(null);
  const [job, setJob] = useState(null);
  const [serverJob, setServerJob] = useState(null);
  const [checking, setChecking] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [serverDownloading, setServerDownloading] = useState(false);
  const [error, setError] = useState('');
  const [appError, setAppError] = useState('');
  const [serverError, setServerError] = useState('');

  // Checking is always explicit. Only a manually started server job is polled.
  const check = useCallback(async (force = false) => {
    setChecking(true); setError(''); setAppError(''); setServerError('');
    if (isClient) {
      const [appResult, serverResult] = await Promise.allSettled([checkNativeAppUpdate(), getRuntimeUpdate(force)]);
      if (appResult.status === 'fulfilled') setAppSnapshot(appResult.value || null);
      else setAppError(errorText(appResult.reason));
      if (serverResult.status === 'fulfilled') {
        setServerSnapshot(serverResult.value || null);
        if (serverResult.value?.job) setServerJob(serverResult.value.job);
        if (serverResult.value?.error) setServerError(serverResult.value.error);
      } else setServerError(errorText(serverResult.reason));
      setChecking(false);
      return;
    }
    try {
      const value = nativeApp ? await checkNativeAppUpdate() : await getRuntimeUpdate(force);
      setSnapshot(value || null);
      if (value?.job) setJob(value.job);
      if (value?.error) setError(value.error);
    } catch (reason) { setError(errorText(reason)); }
    finally { setChecking(false); }
  }, [isClient, nativeApp]);

  const pollJob = useCallback((currentJob, setter) => {
    if (!currentJob?.job_id || !['downloading', 'staged', 'requested'].includes(currentJob.state)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await getRuntimeUpdateJob(currentJob.job_id);
        setter(next);
        if (['completed', 'failed', 'downloaded'].includes(next.state)) window.clearInterval(timer);
      } catch (_error) { /* service restart during upgrade is expected */ }
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => pollJob(job, setJob), [job, pollJob]);
  useEffect(() => pollJob(serverJob, setServerJob), [serverJob, pollJob]);

  const startServerDownload = async () => {
    setServerDownloading(true); setServerError('');
    try {
      const value = await startRuntimeUpdateDownload();
      setServerJob(value);
      setJob(value);
      message.success('服务端更新已提交，服务将短暂重启');
    } catch (reason) { setServerError(errorText(reason)); }
    finally { setServerDownloading(false); }
  };
  const downloadApp = async () => {
    setDownloading(true); setAppError('');
    try {
      const value = await installNativeAppUpdate();
      setAppSnapshot(value || appSnapshot);
      if (isLocal) setSnapshot(value || snapshot);
      message.success('客户端更新包已准备，请按系统提示完成安装');
    } catch (reason) { setAppError(errorText(reason)); }
    finally { setDownloading(false); }
  };
  const downloadServer = () => {
    if (serverSnapshot?.capability === 'linux-auto') {
      Modal.confirm({
        title: '确认更新服务端？',
        content: '服务端会在校验更新包后短暂重启，配置、登录状态和业务数据会保留。',
        okText: '确认更新', cancelText: '取消', onOk: startServerDownload,
      });
      return;
    }
    return startServerDownload();
  };

  const renderStatus = (value, status, valueError) => (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      {status && <Text type={value?.state === 'failed' ? 'danger' : 'secondary'}>{status}</Text>}
      {value?.error && <Text type="danger">{value.error}</Text>}
      {value?.state === 'downloaded' && value.path && <Text type="secondary">文件位置：{value.path}</Text>}
      {valueError && <Text type="danger">{valueError}</Text>}
    </Space>
  );
  const renderServerPanel = () => {
    const available = Boolean(serverSnapshot?.available || serverSnapshot?.update_available);
    const status = serverJob ? stateText(serverJob.state) : '';
    return <div className="version-update-panel">
      <Space wrap>
        <Text strong>连接的服务端</Text>
        {serverSnapshot?.current_version && <Text>当前版本：{serverSnapshot.current_version}</Text>}
        {serverSnapshot?.latest_version && <Text type="secondary">最新版本：{serverSnapshot.latest_version}</Text>}
        {available ? <Tag color="blue">有新版本</Tag> : serverSnapshot && !serverError ? <Tag color="green">已是最新</Tag> : null}
      </Space>
      {renderStatus(serverJob, status, serverError)}
      <Space wrap>
        {available && serverSnapshot?.capability !== 'none' && <Button type="primary" icon={<CloudDownloadOutlined />} loading={serverDownloading} onClick={downloadServer}>{serverSnapshot.capability === 'linux-auto' ? '下载并升级服务端' : '下载服务端更新'}</Button>}
        <Button icon={<LinkOutlined />} href={serverSnapshot?.release_url || 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest'} target="_blank" rel="noreferrer">查看服务端 Release</Button>
      </Space>
      <Text type="secondary">服务端更新需要由你手动点击下载；不会因为进入设置页或手机客户端启动而自动更新。</Text>
    </div>;
  };

  if (isClient) {
    return <Card title="应用与服务版本" className="system-settings-card" extra={<Button icon={<ReloadOutlined />} loading={checking} onClick={() => check(true)}>检查更新</Button>}>
      <Space direction="vertical" size={14} style={{ width: '100%' }}>
        <div className="version-update-panel">
          <Space wrap>
            <Text strong>手机客户端</Text><Text>当前版本：{shellInfo?.version || '未知'}</Text>
            {appSnapshot?.latest_version && <Text type="secondary">最新版本：{appSnapshot.latest_version}</Text>}
            {appSnapshot?.available ? <Tag color="blue">有新版本</Tag> : appSnapshot && !appError ? <Tag color="green">已是最新</Tag> : null}
          </Space>
          {appError && <Text type="danger">{appError}</Text>}
          <Space wrap>
            {appSnapshot?.available && <Button type="primary" icon={<CloudDownloadOutlined />} loading={downloading} onClick={downloadApp}>下载并安装客户端</Button>}
            <Button icon={<LinkOutlined />} href={appSnapshot?.release_url || 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest'} target="_blank" rel="noreferrer">查看客户端 Release</Button>
          </Space>
          <Text type="secondary">客户端 APK 只在你点击下载后获取，并由 Android 系统安装器确认。</Text>
        </div>
        {renderServerPanel()}
      </Space>
    </Card>;
  }

  const current = snapshot?.current_version || shellInfo?.version || '未知';
  const latest = snapshot?.latest_version;
  const available = Boolean(snapshot?.available || snapshot?.update_available);
  const capability = snapshot?.capability || (nativeApp ? 'android-install' : 'none');
  const status = job ? stateText(job.state) : '';
  const releaseUrl = snapshot?.release_url || 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest';
  return <Card title="应用版本" className="system-settings-card" extra={<Button icon={<ReloadOutlined />} loading={checking} onClick={() => check(true)}>检查更新</Button>}>
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space wrap><Text>当前版本：{current}</Text>{latest && <Text type="secondary">最新版本：{latest}</Text>}{available ? <Tag color="blue">有新版本</Tag> : snapshot && !error ? <Tag color="green">已是最新</Tag> : null}</Space>
      {renderStatus(job, status, error)}
      <Space wrap>
        {available && capability !== 'none' && <Button type="primary" icon={<CloudDownloadOutlined />} loading={downloading} onClick={() => { if (nativeApp) return downloadApp(); return capability === 'linux-auto' ? downloadServer() : startServerDownload(); }}>{capability === 'linux-auto' ? '下载并升级' : capability === 'android-install' ? '下载并安装' : '下载更新'}</Button>}
        <Button icon={<LinkOutlined />} href={releaseUrl} target="_blank" rel="noreferrer">查看 Release</Button>
      </Space>
      <Text type="secondary">更新只在你手动点击检查、下载或升级后执行，不会在进入设置页时自动检查或下载。</Text>
    </Space>
  </Card>;
}
