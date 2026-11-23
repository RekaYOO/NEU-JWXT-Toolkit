import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Form, Input, Modal, Space, Spin, Tag, Typography } from 'antd';
import { addAdminAccount, changeAdminAccount, getAdminAccounts, getAdminStatus, loginAdmin, logoutAdmin, logoutAccessGateway } from '../services/api';
import './AdminPage.css';
import SiteAppearanceSettings from '../theme/SiteAppearanceSettings';

const actions = {
  stop: ['停止运行', '结束该账号的登录和后台任务，保留账号准入及已有资料。再次登录后，自动任务仍需手动开启。'],
  disable: ['停用账号', '结束登录和后台任务，并禁止该账号再次登录。已有资料保留，可恢复准入。'],
  remove: ['移出准入名单', '撤销账号准入并结束登录和后台任务。已有资料保留，可通过恢复准入重新启用。'],
  enable: ['恢复准入', '允许该账号重新登录。不会自动恢复之前停止的任务。'],
};

export default function AdminPage({ siteAccessRequired = false }) {
  const [status, setStatus] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState(null);
  const locked = useRef(false);
  const [passwordForm] = Form.useForm();
  const [accountForm] = Form.useForm();

  const showError = useCallback(failure => {
    if (failure.response?.status === 401) {
      setStatus(previous => ({ ...previous, authenticated: false }));
      setAccounts([]);
      setPending(null);
    }
    setError(failure.message || '操作失败，请重试');
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next = await getAdminStatus();
      setStatus(next);
      if (next.authenticated) setAccounts((await getAdminAccounts()).accounts || []);
      else setAccounts([]);
    } catch (failure) {
      showError(failure);
    } finally {
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

  return (
    <main className="admin-page">
      <header className="admin-header">
        <div><Typography.Title level={2}>账号管理</Typography.Title><Typography.Text type="secondary">管理使用权限与后台运行</Typography.Text></div>
        <Space wrap>
          <Button href="/">返回工具箱</Button>
          {status?.authenticated && <Button disabled={busy || loading} onClick={() => run(async () => {
            await logoutAdmin();
            setStatus(previous => ({ ...previous, authenticated: false }));
            setAccounts([]);
            setPending(null);
            setNotice('已退出管理');
          })}>退出管理</Button>}
          {siteAccessRequired && <Button disabled={busy || loading} onClick={() => run(async () => {
            await logoutAccessGateway();
            window.location.replace('/');
          })}>退出站点</Button>}
        </Space>
      </header>
      {error && <Alert type="error" showIcon message={error} />}
      {notice && <Alert type="success" showIcon message={notice} />}
      {loading ? <div className="admin-loading" role="status"><Spin /><span>正在读取管理状态</span></div> : !status ? (
        <Button onClick={refresh}>重试</Button>
      ) : !status.configured ? (
        <Alert type="info" showIcon message="尚未初始化管理员密码" description="请由服务器维护者在启动配置中初始化管理员密码，再刷新此页面。" />
      ) : !status.authenticated ? (
        <Form form={passwordForm} name="toolkit-admin" className="admin-login" layout="vertical" onFinish={({ adminPassword }) => run(async () => {
          try { await loginAdmin(adminPassword); } finally { passwordForm.resetFields(); }
          await refresh();
        })}>
          <Form.Item label="管理员密码" name="adminPassword" rules={[{ required: true, message: '请输入管理员密码' }]}>
            <Input.Password autoComplete="section-toolkit-admin current-password" disabled={busy} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy}>进入管理</Button>
        </Form>
      ) : (
        <>
          <Alert type="info" showIcon message={status.deployment_mode === 'remote-single' ? '远程单用户模式：最多一个启用账号' : '远程多用户模式：仅准入账号可以登录'} description="管理权限仅用于账号准入和停止运行，不提供个人教务资料或学校凭据。" />
          <SiteAppearanceSettings />
          <Form form={accountForm} layout="vertical" className="admin-add" onFinish={({ account }) => run(async () => {
            await addAdminAccount(account.trim());
            accountForm.resetFields();
            setNotice('账号已加入准入名单');
            await refresh();
          })}>
            <Form.Item label="准入学号" name="account" rules={[{ required: true, whitespace: true, message: '请输入学号' }]}>
              <Input maxLength={64} autoComplete="off" disabled={busy} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={busy}>添加账号</Button>
            <Button disabled={busy} onClick={refresh}>刷新列表</Button>
          </Form>
          {accounts.length === 0 ? <Empty description="尚未添加准入账号" /> : <ul className="admin-accounts" aria-label="准入账号">
            {accounts.map(account => <li key={account.account}>
              <div className="admin-account-identity"><strong>{account.account}</strong><Tag color={account.enabled ? 'success' : 'default'}>{account.enabled ? '准入已启用' : '准入已停用'}</Tag></div>
              <div className="admin-actions">
                {(account.enabled ? ['stop', 'disable', 'remove'] : ['enable']).map(action => (
                  <Button key={action} danger={action !== 'enable'} disabled={busy} onClick={() => setPending({ account: account.account, action })}>{actions[action][0]}</Button>
                ))}
              </div>
            </li>)}
          </ul>}
        </>
      )}
      <Modal title={pending ? `${actions[pending.action][0]} · ${pending.account}` : ''} open={Boolean(pending)} confirmLoading={busy} closable={!busy} maskClosable={!busy} cancelButtonProps={{ disabled: busy }} okText="确认" cancelText="取消" onCancel={() => setPending(null)} onOk={() => pending && run(async () => {
        await changeAdminAccount(pending.account, pending.action);
        setNotice(`${pending.account}：${actions[pending.action][0]}成功`);
        setPending(null);
        await refresh();
      })}>
        <p>{pending && actions[pending.action][1]}</p>
        {error && <Alert type="error" showIcon message={error} />}
      </Modal>
    </main>
  );
}
