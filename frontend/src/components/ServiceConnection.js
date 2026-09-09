import React, {
  forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState,
} from 'react';
import { Alert, Button, Checkbox, Input, Modal, QRCode, Radio, Space, Typography, message } from 'antd';
import { LockOutlined, QrcodeOutlined, ReloadOutlined, UserOutlined } from '@ant-design/icons';
import {
  startWebVPNQRLogin, getWebVPNQRStatus, cancelWebVPNQRLogin, startWebVPNPasswordLogin,
  refreshWebVPNCaptcha, sendWebVPNSMSCode, verifyWebVPNSMSCode, cancelWebVPNSMSLogin,
  getWebVPNErrorMessage, isWebVPNFlowInvalid, isWebVPNCampusNetworkBlocked,
  getPendingAuthChallenge,
} from '../services/api';
import WebVPNAuthModal from './WebVPNAuthModal';
import './ServiceConnection.css';

const activeLoginTargets = new Set();
export const hasServiceAuthOwner = service => activeLoginTargets.has(service);

export function ServiceRouteControl({ status, busy, saving = busy, offline, onChange, onCheck }) {
  return (
    <div className="service-route-control">
      <Radio.Group value={status?.network_mode || 'follow'} disabled={saving || offline}
        onChange={event => onChange(event.target.value)}>
        <Radio.Button value="follow">跟随教务</Radio.Button>
        <Radio.Button value="direct">直连</Radio.Button>
        <Radio.Button value="webvpn">WebVPN</Radio.Button>
      </Radio.Group>
      <Typography.Text type="secondary">
        {offline ? '离线模式' : `当前有效线路：${status?.effective_network_mode === 'webvpn'
          ? 'WebVPN' : status?.effective_network_mode === 'direct' ? '直连' : '待核验'}`}
        {busy ? '（正在核验）' : ''}
      </Typography.Text>
      {onCheck && <Button icon={<ReloadOutlined />} disabled={offline} loading={busy}
        onClick={onCheck}>重新检测</Button>}
    </div>
  );
}

export function ServiceAuthNotice({ status, busy, offline, onChange, onCheck, onLogin, className = '' }) {
  if (offline || !status || status.service_authenticated) return null;
  const state = status.service_auth_state;
  const blocked = state === 'campus_network_blocked';
  const webvpn = status.effective_network_mode === 'webvpn';
  return (
    <Alert className={`service-auth-notice ${className}`} showIcon
      type={busy || state === 'checking' ? 'info' : state === 'login_required' ? 'warning' : 'error'}
      message={status.message || '服务会话需要重新核验'}
      action={busy ? null : (
        <Space wrap>
          {blocked ? <>
            <Button size="small" type="primary" onClick={() => onChange('direct')}>切换为直连</Button>
            <Button size="small" onClick={() => onChange('follow')}>跟随教务</Button>
          </> : state === 'login_required' && webvpn && status.primary_authenticated ? <>
            <Button size="small" type="primary" icon={<LockOutlined />} onClick={() => onLogin('password')}>账号密码恢复</Button>
            <Button size="small" icon={<QrcodeOutlined />} onClick={() => onLogin('qr')}>微信扫码恢复</Button>
          </> : <>
            {!webvpn && ['network_unreachable', 'login_required'].includes(state)
              && <Button size="small" type="primary" onClick={() => onChange('webvpn')}>切换 WebVPN</Button>}
            <Button size="small" onClick={onCheck}>重新检查</Button>
          </>}
        </Space>
      )}
    />
  );
}

export const ServiceWebVPNLogin = forwardRef(function ServiceWebVPNLogin({
  service, username = '', offline = false, onAuthenticated, onBlocked,
}, ref) {
  const [view, setView] = useState(null);
  const [account, setAccount] = useState(username);
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [qr, setQr] = useState(null);
  const [sms, setSms] = useState(null);
  const [captcha, setCaptcha] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [captchaBusy, setCaptchaBusy] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const active = useRef({ qr: null, sms: null });
  const callbacks = useRef({});
  callbacks.current = { onAuthenticated, onBlocked };
  active.current = { qr, sms };

  const discard = useCallback(() => {
    const previous = active.current;
    active.current = { qr: null, sms: null };
    generation.current += 1;
    setQr(null); setSms(null); setView(null); setPassword('');
    setCaptcha(''); setCode(''); setSent(false); setError(''); setBusy(false); setCaptchaBusy(false);
    if (previous.qr?.flow_id) cancelWebVPNQRLogin(previous.qr.flow_id).catch(() => {});
    if (previous.sms?.flow_id) cancelWebVPNSMSLogin(previous.sms.flow_id).catch(() => {});
  }, []);

  const applyResult = useCallback(async result => {
    if (isWebVPNCampusNetworkBlocked(result)) {
      discard();
      callbacks.current.onBlocked?.(result);
      return;
    }
    if (result.success && result.status === 'authenticated') {
      generation.current += 1;
      active.current = { qr: null, sms: null };
      setView(null); setQr(null); setSms(null); setPassword(''); setError('');
      setBusy(false); setCaptchaBusy(false);
      message.success('WebVPN 登录已恢复');
      await callbacks.current.onAuthenticated?.(result);
    } else if (result.success && result.status === 'sms_required') {
      setView(null); setQr(null); setSms(result); setPassword('');
      setCaptcha(''); setCode(''); setSent(false);
    } else if (result.status === 'session_pending' && result.sms_verified) {
      setSms(previous => ({ ...previous, ...result }));
      setCode(''); setError(result.message || '');
    } else if (!result.success) {
      setError(getWebVPNErrorMessage(result, 'WebVPN 登录失败'));
      if (isWebVPNFlowInvalid(result)) { setQr(null); setSms(null); }
    }
  }, [discard]);

  const open = useCallback(async nextView => {
    if (offline) return;
    discard();
    const current = generation.current;
    setAccount(username || '');
    // Continue the original silent-recovery Session instead of resubmitting credentials.
    try {
      const pending = await getPendingAuthChallenge();
      if (current !== generation.current) return;
      if (pending.required && pending.target_service === service) {
        setSms(pending);
        return;
      }
    } catch (_) { /* A fresh foreground flow remains available. */ }
    if (current !== generation.current) return;
    setView(nextView);
    if (nextView !== 'qr') return;
    setBusy(true);
    try {
      const result = await startWebVPNQRLogin(username, service);
      if (current !== generation.current) {
        if (result.flow_id) await cancelWebVPNQRLogin(result.flow_id).catch(() => {});
        return;
      }
      if (result.success) setQr(result);
      else await applyResult(result);
    } catch (e) {
      if (current === generation.current) setError(getWebVPNErrorMessage(e, '获取二维码失败'));
    } finally { if (current === generation.current) setBusy(false); }
  }, [applyResult, discard, offline, service, username]);

  useImperativeHandle(ref, () => ({ open, close: discard }), [open, discard]);
  useEffect(() => {
    if (offline) return undefined;
    activeLoginTargets.add(service);
    const pending = event => {
      const flow = event.detail;
      if (flow?.required && flow.target_service === service
          && active.current.sms?.flow_id !== flow.flow_id) {
        generation.current += 1;
        setView(null); setQr(null); setSms(flow); setCaptcha(''); setCode(''); setSent(false);
        setBusy(false); setCaptchaBusy(false);
      }
    };
    window.addEventListener('neu-auth-pending', pending);
    return () => {
      activeLoginTargets.delete(service);
      window.removeEventListener('neu-auth-pending', pending);
    };
  }, [offline, service]);
  useEffect(() => () => {
    generation.current += 1;
    const flows = active.current;
    if (flows.qr?.flow_id) cancelWebVPNQRLogin(flows.qr.flow_id).catch(() => {});
    if (flows.sms?.flow_id) cancelWebVPNSMSLogin(flows.sms.flow_id).catch(() => {});
  }, []);
  useEffect(() => {
    if (!qr?.flow_id) return undefined;
    let stopped = false;
    const current = generation.current;
    let timer;
    const poll = async () => {
      try {
        const result = await getWebVPNQRStatus(qr.flow_id);
        if (stopped || current !== generation.current) return;
        await applyResult(result);
        if (['expired', 'missing', 'error'].includes(result.status)) {
          setQr(null); setError(getWebVPNErrorMessage(result, '二维码已失效，请重新获取'));
          return;
        }
      } catch (e) {
        if (!stopped) setError(getWebVPNErrorMessage(e, '暂时无法检查二维码状态'));
      }
      if (!stopped) timer = window.setTimeout(poll, Math.max(1, Number(qr.poll_interval || 3)) * 1000);
    };
    poll();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [qr, applyResult]);

  const perform = async (operation, kind) => {
    if (busy || captchaBusy || offline) return;
    const current = generation.current;
    (kind === 'captcha' ? setCaptchaBusy : setBusy)(true);
    setError('');
    try {
      const result = await operation();
      if (current !== generation.current) {
        if (result.status === 'sms_required' && result.flow_id) {
          await cancelWebVPNSMSLogin(result.flow_id).catch(() => {});
        }
        return;
      }
      if (result.captcha_invalid || result.status === 'captcha_invalid') {
        setSms(prev => ({ ...prev, ...result }));
        setCaptcha(''); setCode(''); setSent(false);
        setError(getWebVPNErrorMessage(result, '图形验证码不正确'));
      } else if (kind === 'captcha' && result.success) {
        setSms(prev => ({ ...prev, ...result }));
        setCaptcha(''); setSent(false);
      } else if (kind === 'send' && result.success) {
        setSms(prev => ({ ...prev, ...result })); setSent(true);
      } else await applyResult(result);
    } catch (e) {
      if (current === generation.current) {
        setError(getWebVPNErrorMessage(e, '认证请求失败'));
        if (kind === 'captcha') setSms(prev => prev ? { ...prev, captcha_image: null } : null);
        if (isWebVPNFlowInvalid(e)) setSms(null);
        if (isWebVPNCampusNetworkBlocked(e)) { discard(); callbacks.current.onBlocked?.(e); }
      }
    } finally {
      if (current === generation.current) { setBusy(false); setCaptchaBusy(false); }
    }
  };

  return <>
    <Modal open={Boolean(view) && !offline} title="登录 WebVPN" footer={null}
      zIndex={1100} onCancel={discard} destroyOnHidden>
      <div className="service-webvpn-login">
        {error && <Alert type="warning" showIcon message={error} />}
        {view === 'password' ? <>
          <Input prefix={<UserOutlined />} value={account} onChange={e => setAccount(e.target.value)}
            placeholder="学号" autoComplete={`section-${service}-webvpn username`} />
          <Input.Password prefix={<LockOutlined />} value={password} onChange={e => setPassword(e.target.value)}
            placeholder="密码" autoComplete={`section-${service}-webvpn current-password`} />
          <Checkbox checked={remember} onChange={e => setRemember(e.target.checked)}>记住密码（本地保存）</Checkbox>
          <Button type="primary" block loading={busy} disabled={!account.trim() || !password}
            onClick={() => perform(() => startWebVPNPasswordLogin(account.trim(), password, remember, service))}>恢复 WebVPN 登录</Button>
          <Button type="link" icon={<QrcodeOutlined />} onClick={() => open('qr')}>改用微信扫码</Button>
        </> : <>
          {qr?.qr_content && <QRCode value={qr.qr_content} size={210} />}
          <Button icon={<QrcodeOutlined />} loading={busy} onClick={() => open('qr')}>重新获取二维码</Button>
          <Button type="link" onClick={() => open('password')}>返回账号密码恢复</Button>
        </>}
      </div>
    </Modal>
    <WebVPNAuthModal zIndex={1100} flow={offline ? null : sms} captchaCode={captcha} setCaptchaCode={setCaptcha}
      smsCode={code} setSmsCode={setCode} smsSent={sent} error={error} loading={busy} captchaLoading={captchaBusy}
      onRefreshCaptcha={() => perform(() => refreshWebVPNCaptcha(sms.flow_id), 'captcha')}
      onSendSMS={() => captcha.trim() && perform(() => sendWebVPNSMSCode(sms.flow_id, captcha.trim()), 'send')}
      onVerify={() => (sms?.sms_verified || code.trim()) && perform(() => verifyWebVPNSMSCode(sms.flow_id, code.trim()), 'verify')}
      onCancel={discard} />
  </>;
});
