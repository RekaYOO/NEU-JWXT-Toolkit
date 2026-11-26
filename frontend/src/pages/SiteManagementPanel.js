import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Dropdown, Empty, Form, Input, Modal, Segmented, Spin, Tabs, Tag } from 'antd';
import {
  MoreOutlined, PlusOutlined,
  ReloadOutlined, SafetyCertificateOutlined, SkinOutlined, TeamOutlined,
} from '@ant-design/icons';
import { addAdminAccount, changeAdminAccount, getAdminAccounts } from '../services/api';
import './SiteManagementPanel.css';
import SiteAppearanceSettings from '../theme/SiteAppearanceSettings';

const actions = {
  stop: ['停止运行', '结束该账号的登录和后台任务，保留账号准入及已有资料。再次登录后，自动任务仍需手动开启。'],
  disable: ['停用账号', '结束登录和后台任务，并禁止该账号再次登录。已有资料保留，可恢复准入。'],
  remove: ['移出准入名单', '撤销账号准入并结束登录和后台任务。已有资料保留，可通过恢复准入重新启用。'],
  enable: ['恢复准入', '允许该账号重新登录。不会自动恢复之前停止的任务。'],
};

const accountFilters = [
  { label: '全部', value: 'all' }, { label: '已准入', value: 'enabled' }, { label: '未准入', value: 'disabled' },
];

export default function SiteManagementPanel({ onThemeSaved }) {
  const [deploymentMode, setDeploymentMode] = useState('');
  const [accounts, setAccounts] = useState([]);
  const [defaultAdministrator, setDefaultAdministrator] = useState('');
  const [accountsReady, setAccountsReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState(null);
  const [tab, setTab] = useState('accounts');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const locked = useRef(false);
  const reading = useRef(false);
  const [unauthorized, setUnauthorized] = useState(false);
  const [accountForm] = Form.useForm();

  const showError = useCallback(failure => {
    if (failure.response?.status === 401) {
      setUnauthorized(true);
      setAccounts([]);
      setAccountsReady(false);
      setPending(null);
      setTab('accounts');
      setQuery('');
      setFilter('all');
    }
    setError(failure.message || '操作失败，请重试');
  }, []);

  const refresh = useCallback(async () => {
    if (reading.current) return;
    reading.current = true;
    setLoading(true);
    setError('');
    try {
      const result = await getAdminAccounts();
      setAccounts(result.accounts || []);
      setDefaultAdministrator(result.default_administrator || '');
      setDeploymentMode(result.deployment_mode);
      setAccountsReady(true);
      setUnauthorized(false);
    } catch (failure) {
      showError(failure);
    } finally {
      reading.current = false;
      setLoading(false);
    }
  }, [showError]);

  useEffect(() => { refresh(); }, [refresh]);

  const run = async operation => {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await operation();
    } catch (failure) {
      showError(failure);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  };

  const enabled = accounts.filter(account => account.enabled).length;
  const single = deploymentMode === 'remote-single';
  const full = single && enabled > 0;
  const visible = accounts.filter(account => account.account.includes(query.trim())
    && (filter === 'all' || account.enabled === (filter === 'enabled')));
  const updating = loading || busy;
  const admission = (
    <div className="admin-workspace">
      <section className="admin-overview" aria-label="准入概况">
        <div><span>名单账号</span><strong>{accountsReady ? accounts.length : '—'}</strong></div>
        <div><span>已准入</span><strong>{accountsReady ? enabled : '—'}</strong></div>
        <div><span>未准入</span><strong>{accountsReady ? accounts.length - enabled : '—'}</strong></div>
      </section>
      <section className="admin-panel" aria-labelledby="admin-admission-heading" aria-busy={loading}>
        <div className="admin-panel-heading">
          <div><h2 id="admin-admission-heading">准入名单</h2><p>只有已准入的账号可以登录工具箱。</p></div>
          <Button icon={<ReloadOutlined />} loading={loading} disabled={busy} onClick={refresh}>刷新列表</Button>
        </div>
        <Form form={accountForm} name="toolkit-admission" layout="vertical" className="admin-add" onFinish={({ account }) => run(async () => {
          const next = account.normalize('NFKC').trim();
          const existing = accounts.find(item => item.account === next);
          if (existing) throw new Error(existing.enabled ? '该学号已在准入名单中' : '该学号已有记录，请使用“恢复准入”');
          await addAdminAccount(next);
          accountForm.resetFields();
          setQuery('');
          setFilter('all');
          setNotice('账号已加入准入名单');
          await refresh();
        })}>
          <Form.Item label="准入学号" name="account" rules={[{ required: true, whitespace: true, message: '请输入学号' }]}>
            <Input maxLength={64} autoComplete="off" disabled={updating || full || !accountsReady} placeholder="输入要允许登录的学号" />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<PlusOutlined />} disabled={updating || full || !accountsReady}>添加账号</Button>
        </Form>
        {full && <p className="admin-capacity">单用户模式仅允许当前默认管理员登录。</p>}
        {accountsReady && accounts.length > 0 && <div className="admin-filters">
          <Input aria-label="筛选学号" allowClear placeholder="筛选学号" value={query} onChange={event => setQuery(event.target.value)} />
          <Segmented aria-label="准入状态筛选" options={accountFilters} value={filter} onChange={setFilter} />
        </div>}
        {!accountsReady ? (
          loading ? <div className="admin-loading" role="status"><Spin /><span>正在读取准入名单</span></div> : <Empty description="准入名单暂不可用" />
        ) : visible.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={accounts.length ? '没有符合条件的账号' : '尚未添加准入账号'} />
        ) : <ul className="admin-accounts" aria-label="准入账号">
          {visible.map(account => <li key={account.account}>
            <div className="admin-account-identity">
              <TeamOutlined aria-hidden="true" />
              <strong>{account.account}</strong>
              <Tag color={account.enabled ? 'success' : 'default'}>{account.enabled ? '准入已启用' : '准入已停用'}</Tag>
              {account.account === defaultAdministrator && <Tag>默认管理员</Tag>}
            </div>
            <div className="admin-actions">
              {(account.enabled ? (account.account === defaultAdministrator ? ['stop'] : ['stop', 'disable']) : ['enable']).map(action => (
                <Button key={action} danger={action === 'disable'} disabled={updating || action === 'enable' && full} onClick={() => { setError(''); setPending({ account: account.account, action }); }}>{actions[action][0]}</Button>
              ))}
              {account.enabled && account.account !== defaultAdministrator && <Dropdown trigger={['click']} disabled={updating} menu={{
                items: [{ key: 'remove', label: actions.remove[0], danger: true }],
                onClick: () => { setError(''); setPending({ account: account.account, action: 'remove' }); },
              }}><Button aria-label={`${account.account}的更多操作`} icon={<MoreOutlined />} /></Dropdown>}
            </div>
          </li>)}
        </ul>}
      </section>
    </div>
  );

  if (unauthorized) return <Alert type="info" showIcon message={error || '请重新使用指定的管理员学号登录'} />;

  return (
    <section className="admin-content">
        {deploymentMode && <Tag className="admin-mode" icon={<SafetyCertificateOutlined />}>{single ? '远程单用户' : '远程多用户'}</Tag>}
        {error && <Alert type="error" showIcon message={error} />}
        {notice && <Alert type="success" showIcon message={notice} />}
        <Tabs activeKey={tab} onChange={setTab} items={[
          { key: 'accounts', label: <><TeamOutlined />账号管理</>, children: admission },
          { key: 'appearance', label: <><SkinOutlined />站点外观</>, children: <SiteAppearanceSettings onSaved={onThemeSaved} onUnauthorized={showError} /> },
        ]} />
      <Modal title={pending ? `${actions[pending.action][0]} · ${pending.account}` : ''} open={Boolean(pending)} confirmLoading={busy} closable={!busy} maskClosable={!busy} keyboard={!busy} cancelButtonProps={{ disabled: busy }} okButtonProps={{ danger: ['disable', 'remove'].includes(pending?.action) }} okText="确认" cancelText="取消" onCancel={() => setPending(null)} onOk={() => pending && run(async () => {
        await changeAdminAccount(pending.account, pending.action);
        setNotice(`${pending.account}：${actions[pending.action][0]}成功`);
        setPending(null);
        await refresh();
      })}>
        <p>{pending && actions[pending.action][1]}</p>
        {error && <Alert type="error" showIcon message={error} />}
      </Modal>
    </section>
  );
}
