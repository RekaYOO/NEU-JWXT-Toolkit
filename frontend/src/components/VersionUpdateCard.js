import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Modal, Space, Tag, Typography, message } from 'antd';
import { CloudDownloadOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons';
import { getRuntimeUpdate, getRuntimeUpdateJob, startRuntimeUpdateDownload } from '../services/api';
import { checkNativeAppUpdate, installNativeAppUpdate, nativeShellInfo } from '../services/nativeBridge';

const { Text } = Typography;
const stateText = state => ({
  downloading: '正在下载更新包', verifying: '正在校验更新包',
  staged: '更新包已准备', requested: '等待服务端更新器接管',
  installing: '正在安装并检查新版本', downloaded: '更新包已下载，请手动安装',
  completed: '更新已完成', failed: '更新失败',
}[state] || '尚未检查');
const errorText = reason => reason?.response?.data?.detail || reason?.message || '版本检查失败';
const activeStates = ['downloading', 'verifying', 'staged', 'requested', 'installing'];
const formatTime = value => {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};

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
      await Promise.all([
        checkNativeAppUpdate().then(
          value => setAppSnapshot(value || null),
          reason => setAppError(errorText(reason)),
        ),
        getRuntimeUpdate(force).then(value => {
          setServerSnapshot(value || null);
          setServerJob(value?.job || null);
          if (value?.error) setServerError(value.error);
        }, reason => setServerError(errorText(reason))),
      ]);
      setChecking(false);
      return;
    }
    try {
      const value = nativeApp ? await checkNativeAppUpdate() : await getRuntimeUpdate(force);
      setSnapshot(value || null);
      if (!nativeApp) setJob(value?.job || null);
      if (value?.error) setError(value.error);
    } catch (reason) { setError(errorText(reason)); }
    finally { setChecking(false); }
  }, [isClient, nativeApp]);

  const refreshCompletedVersion = useCallback(async () => {
    try {
      const value = await getRuntimeUpdate(true);
      if (isClient) setServerSnapshot(value);
      else setSnapshot(value);
    } catch (_error) { /* the completed job remains visible if release metadata is unavailable */ }
  }, [isClient]);
  const pollJob = useCallback((jobId, state, setter, setJobError) => {
    if (!jobId || !activeStates.includes(state)) return undefined;
    let timer;
    let stopped = false;
    let failures = 0;
    const poll = async () => {
      let terminal = false;
      try {
        const next = await getRuntimeUpdateJob(jobId);
        if (stopped) return;
        setter(next);
        failures = 0;
        setJobError('');
        terminal = !activeStates.includes(next.state);
        if (next.state === 'completed') void refreshCompletedVersion();
      } catch (_error) {
        if (stopped) return;
        failures += 1;
        if (failures >= 3) setJobError('暂时无法读取更新进度，服务可能正在重启；恢复连接后会继续检查。');
      }
      if (!stopped && !terminal) timer = window.setTimeout(poll, 2500);
    };
    timer = window.setTimeout(poll, 2000);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [refreshCompletedVersion]);
  const jobId = job?.job_id;
  const jobState = job?.state;
  const serverJobId = serverJob?.job_id;
  const serverJobState = serverJob?.state;
  useEffect(() => pollJob(jobId, jobState, setJob, setError), [jobId, jobState, pollJob]);
  useEffect(() => pollJob(serverJobId, serverJobState, setServerJob, setServerError), [serverJobId, serverJobState, pollJob]);

  const startServerDownload = async () => {
    setServerDownloading(true); setServerError('');
    try {
      const value = await startRuntimeUpdateDownload();
      if (isClient) setServerJob(value);
      else setJob(value);
      message.info('更新任务已开始，请在下方查看实际进度');
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
    if ((isClient ? serverSnapshot : snapshot)?.capability === 'linux-auto') {
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
    <div className="version-update-status" aria-live="polite">
      {status && <Text strong type={value?.state === 'failed' ? 'danger' : undefined}>{status}</Text>}
      {value?.state === 'downloading' && value.bytes_total > 0 && (
        <Text type="secondary">已下载 {Math.min(100, Math.round((value.bytes_downloaded || 0) / value.bytes_total * 100))}%</Text>
      )}
      {value?.history?.length > 0 && <ol className="version-update-history" aria-label="更新过程">
        {value.history.map((entry, index) => <li key={`${entry.state}-${entry.at}-${index}`}>
          <time dateTime={entry.at}>{formatTime(entry.at)}</time>
          <span>{entry.message || stateText(entry.state)}</span>
        </li>)}
      </ol>}
      {value?.error && <Text type="danger">{value.error}</Text>}
      {value?.state === 'downloaded' && value.path && <Text type="secondary">文件位置：{value.path}</Text>}
      {valueError && <Text type="danger">{valueError}</Text>}
    </div>
  );
  const renderServerPanel = () => {
    const available = Boolean(serverSnapshot?.available || serverSnapshot?.update_available);
    const status = serverJob ? stateText(serverJob.state) : '';
    const busy = activeStates.includes(serverJob?.state) || serverDownloading;
    return <section className="version-update-panel" aria-label="连接的服务端更新">
      <div className="version-update-panel__heading"><Text strong>连接的服务端</Text>{serverJob?.state === 'completed' ? <Tag color="green">更新完成</Tag> : available ? <Tag color="blue">有新版本</Tag> : serverSnapshot && !serverError ? <Tag color="green">已是最新</Tag> : null}</div>
       <div className="version-update-version"><span>当前版本：<b>{serverSnapshot?.current_version || '未检查'}</b></span><span>最新版本：<b>{serverSnapshot?.latest_version || '—'}</b></span></div>
      {renderStatus(serverJob, status, serverError)}
      <div className="version-update-actions">
        {available && serverSnapshot?.capability !== 'none' && <Button type="primary" icon={<CloudDownloadOutlined />} loading={serverDownloading} disabled={busy || serverJob?.state === 'completed'} onClick={downloadServer}>{serverSnapshot.capability === 'linux-auto' ? '下载并升级服务端' : '下载服务端更新'}</Button>}
        <Button icon={<LinkOutlined />} href={serverSnapshot?.release_url || 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest'} target="_blank" rel="noreferrer">查看服务端 Release</Button>
      </div>
       <Text type="secondary" className="version-update-hint">{serverSnapshot?.capability === 'linux-manual'
         ? '此服务端的自动更新器未运行。点击后只会下载更新包，需要在服务器解压并执行 sudo ./install.sh --upgrade。'
         : '服务端升级只在你确认后开始；安装结果和失败原因会显示在上方。'}</Text>
     </section>;
  };

  if (isClient) {
    return <Card title="应用与服务版本" className="system-settings-card" extra={<Button icon={<ReloadOutlined />} loading={checking} onClick={() => check(true)}>检查更新</Button>}>
      <Space direction="vertical" size={14} style={{ width: '100%' }}>
        <section className="version-update-panel" aria-label="手机客户端更新">
          <div className="version-update-panel__heading"><Text strong>手机客户端</Text>{appSnapshot?.available ? <Tag color="blue">有新版本</Tag> : appSnapshot && !appError ? <Tag color="green">已是最新</Tag> : null}</div>
           <div className="version-update-version"><span>当前版本：<b>{shellInfo?.version || '未知'}</b></span><span>最新版本：<b>{appSnapshot?.latest_version || '—'}</b></span></div>
          {appError && <Text type="danger">{appError}</Text>}
          <div className="version-update-actions">
            {appSnapshot?.available && <Button type="primary" icon={<CloudDownloadOutlined />} loading={downloading} onClick={downloadApp}>下载并安装客户端</Button>}
            <Button icon={<LinkOutlined />} href={appSnapshot?.release_url || 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest'} target="_blank" rel="noreferrer">查看客户端 Release</Button>
          </div>
          <Text type="secondary" className="version-update-hint">APK 只在你点击下载后获取，并由 Android 系统安装器确认。</Text>
        </section>
        {renderServerPanel()}
      </Space>
    </Card>;
  }

  const current = snapshot?.current_version || shellInfo?.version || '未知';
  const latest = snapshot?.latest_version;
  const available = Boolean(snapshot?.available || snapshot?.update_available);
  const capability = snapshot?.capability || (nativeApp ? 'android-install' : 'none');
  const status = job ? stateText(job.state) : '';
  const busy = activeStates.includes(job?.state) || downloading || serverDownloading;
  const releaseUrl = snapshot?.release_url || 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest';
  return <Card title="应用版本" className="system-settings-card" extra={<Button icon={<ReloadOutlined />} loading={checking} onClick={() => check(true)}>检查更新</Button>}>
    <div className="version-update-single">
      <div className="version-update-panel__heading">{available ? <Tag color="blue">有新版本</Tag> : snapshot && !error ? <Tag color="green">已是最新</Tag> : <Tag>尚未检查</Tag>}</div>
       <div className="version-update-version"><span>当前版本：<b>{current}</b></span><span>最新版本：<b>{latest || '—'}</b></span></div>
      {renderStatus(job, status, error)}
      <div className="version-update-actions">
        {available && capability !== 'none' && <Button type="primary" icon={<CloudDownloadOutlined />} loading={downloading || serverDownloading} disabled={busy || job?.state === 'completed'} onClick={() => { if (nativeApp) return downloadApp(); return capability === 'linux-auto' ? downloadServer() : startServerDownload(); }}>{capability === 'linux-auto' ? '下载并升级' : capability === 'android-install' ? '下载并安装' : '下载更新'}</Button>}
        <Button icon={<LinkOutlined />} href={releaseUrl} target="_blank" rel="noreferrer">查看 Release</Button>
      </div>
      <Text type="secondary" className="version-update-hint">{capability === 'linux-manual'
        ? '自动更新器未运行，下载完成后需在服务器解压并执行 sudo ./install.sh --upgrade。'
        : '更新只在你手动点击后执行，不会自动下载；任务进度和结果显示在上方。'}</Text>
    </div>
  </Card>;
}
