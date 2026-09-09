import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, QRCode, Spin } from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import WebVPNAuthFields from '../components/WebVPNAuthFields';
import {
  getAuthRecoveryStatus,
  pollAuthRecovery,
  refreshAuthRecoveryCaptcha,
  sendAuthRecoverySMS,
  startAuthRecovery,
  verifyAuthRecoverySMS,
} from '../services/api';
import './AuthRecoveryPage.css';

const recoveryErrorMessage = (error, fallback) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return fallback;
};

const formatCountdown = (value) => {
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
};

const AuthRecoveryPage = ({ token }) => {
  const [stage, setStage] = useState('starting');
  const [flow, setFlow] = useState(null);
  const [message, setMessage] = useState('正在读取一次性登录状态');
  const [secondsLeft, setSecondsLeft] = useState(300);
  const [captchaCode, setCaptchaCode] = useState('');
  const [smsCode, setSmsCode] = useState('');
  const [smsSent, setSmsSent] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [authError, setAuthError] = useState('');
  const pollTimer = useRef(null);
  const secondsLeftRef = useRef(300);
  const authenticatedRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const setCountdown = useCallback((value, fallback = 300) => {
    const parsed = Number(value);
    const next = Math.max(1, Number.isFinite(parsed) ? parsed : fallback);
    secondsLeftRef.current = next;
    setSecondsLeft(next);
  }, []);

  const enterSMSStage = useCallback((result) => {
    stopPolling();
    setFlow((current) => ({ ...current, ...result }));
    setCountdown(result.expires_in, 300);
    setCaptchaCode('');
    setSmsCode('');
    setSmsSent(false);
    setAuthError('');
    setStage('sms_required');
    setMessage('请完成短信二次认证');
  }, [setCountdown, stopPolling]);

  const finishAuthenticated = useCallback(() => {
    authenticatedRef.current = true;
    stopPolling();
    setStage('authenticated');
    setMessage('登录已经恢复，相关后台任务将自动继续');
    setFlow(null);
  }, [stopPolling]);

  const handleActionError = (error, fallback) => {
    if (authenticatedRef.current) return;
    const detail = error?.response?.data?.detail;
    const errorCode = detail?.error_code || error?.response?.data?.error_code;
    const text = recoveryErrorMessage(error, fallback);
    if (error?.response?.status === 404) {
      stopPolling();
      setStage('invalid');
      setMessage(text);
      setFlow(null);
    } else if (['WEBVPN_FLOW_MISSING', 'WEBVPN_FLOW_REPLACED', 'WEBVPN_FLOW_EXPIRED'].includes(errorCode)) {
      stopPolling();
      setFlow(null);
      setStage('error');
      setMessage(text);
    } else setAuthError(text);
  };

  const poll = useCallback(async () => {
    try {
      const result = await pollAuthRecovery(token);
      if (authenticatedRef.current) return;
      if (result.status === 'authenticated') {
        finishAuthenticated();
      } else if (result.status === 'sms_required') {
        enterSMSStage(result);
      } else if (result.status === 'expired') {
        stopPolling();
        setStage('expired');
        setMessage('本次二维码已失效，请重新开始登录');
      } else if (result.status === 'error') {
        stopPolling();
        setStage('error');
        setMessage(result.message || '认证失败，请重新开始登录');
      } else if (result.message) {
        setMessage(result.message);
      }
    } catch (error) {
      if (authenticatedRef.current) return;
      if (error.response?.status === 404) {
        stopPolling();
        setStage('invalid');
        setMessage(recoveryErrorMessage(error, '一次性登录链接不存在、已过期或已经完成使用'));
      }
    }
  }, [enterSMSStage, finishAuthenticated, stopPolling, token]);

  const beginPolling = useCallback((pollInterval = 3) => {
    stopPolling();
    pollTimer.current = window.setInterval(
      poll,
      Math.max(1, Number(pollInterval) || 3) * 1000
    );
  }, [poll, stopPolling]);

  const start = useCallback(async () => {
    stopPolling();
    setStage('starting');
    setMessage('正在创建微信扫码二维码');
    setFlow(null);
    setAuthError('');
    setCaptchaCode('');
    setSmsCode('');
    setSmsSent(false);
    try {
      const result = await startAuthRecovery(token);
      if (authenticatedRef.current) return;
      if (result.status === 'authenticated') {
        finishAuthenticated();
        return;
      } else if (result.status === 'sms_required') {
        enterSMSStage(result);
        return;
      }
      setFlow(result);
      setCountdown(result.expires_in, 300);
      setStage('qr_pending');
      setMessage('请使用微信扫码并确认登录');
      beginPolling(result.poll_interval);
    } catch (error) {
      if (authenticatedRef.current) return;
      setStage(error.response?.status === 404 ? 'invalid' : 'error');
      setMessage(recoveryErrorMessage(error, '暂时无法创建二维码，请稍后重试'));
    }
  }, [beginPolling, enterSMSStage, finishAuthenticated, setCountdown, stopPolling, token]);

  const restore = useCallback(async () => {
    try {
      const result = await getAuthRecoveryStatus(token);
      if (authenticatedRef.current) return;
      if (result.status === 'ready' || result.status === 'not_started') {
        await start();
      } else if (['qr_pending', 'pending'].includes(result.status)) {
        setFlow(result);
        setCountdown(result.expires_in, 300);
        setStage('qr_pending');
        setMessage('请使用微信扫码并确认登录');
        beginPolling(result.poll_interval);
      } else if (result.status === 'sms_required') {
        enterSMSStage(result);
      } else {
        await start();
      }
    } catch (error) {
      if (authenticatedRef.current) return;
      setStage(error.response?.status === 404 ? 'invalid' : 'error');
      setMessage(recoveryErrorMessage(error, '暂时无法读取登录恢复状态'));
    }
  }, [beginPolling, enterSMSStage, setCountdown, start, token]);

  useEffect(() => {
    restore();
    return stopPolling;
  }, [restore, stopPolling]);

  useEffect(() => {
    if (!['qr_pending', 'sms_required'].includes(stage)) return undefined;
    const countdown = window.setInterval(() => {
      const previousValue = secondsLeftRef.current;
      const nextValue = Math.max(0, previousValue - 1);
      secondsLeftRef.current = nextValue;
      setSecondsLeft(nextValue);
      if (nextValue === 0 && previousValue > 0) {
        if (stage === 'sms_required') {
          // The school's five-minute SMS validity is approximate.  Keep the
          // form usable and let the official endpoint decide; the user may
          // still retry or explicitly resend without rebuilding the login.
          setMessage('验证码已超过预计有效时间；未收到短信可重新发送，也可直接尝试验证');
        } else {
          stopPolling();
          setStage('expired');
          setMessage('本次二维码已失效，请重新开始登录');
        }
      }
    }, 1000);
    return () => window.clearInterval(countdown);
  }, [stage, stopPolling]);

  const refreshCaptcha = async () => {
    setCaptchaLoading(true);
    setAuthError('');
    try {
      const result = await refreshAuthRecoveryCaptcha(token);
      setFlow((current) => ({ ...current, ...result }));
      setCountdown(result.expires_in, 300);
      setCaptchaCode('');
      setSmsCode('');
      setSmsSent(false);
    } catch (error) {
      handleActionError(error, '刷新图形验证码失败，请重试');
    } finally {
      setCaptchaLoading(false);
    }
  };

  const sendSMS = async () => {
    if (!captchaCode.trim()) {
      setAuthError('请先填写图形验证码');
      return;
    }
    setActionLoading(true);
    setAuthError('');
    try {
      const result = await sendAuthRecoverySMS(token, captchaCode.trim());
      if (result.status === 'captcha_invalid') {
        setFlow((current) => ({ ...current, ...result }));
        setCaptchaCode('');
        setSmsCode('');
        setSmsSent(false);
        setAuthError(result.message || '图形验证码不正确，请重新填写');
        return;
      }
      if (result.success === false || result.status !== 'sent') {
        setAuthError(result.message || '短信验证码发送失败，请重试');
        return;
      }
      setSmsSent(true);
      setSmsCode('');
      setCountdown(result.expires_in, 300);
      setMessage(
        smsSent
          ? '短信验证码已重新发送，请使用最新收到的验证码'
          : '短信验证码已发送，请查收并填写'
      );
    } catch (error) {
      handleActionError(error, '短信验证码发送失败，请重试');
    } finally {
      setActionLoading(false);
    }
  };

  const verifySMS = async () => {
    if (!flow?.sms_verified && !smsCode.trim()) {
      setAuthError('请输入短信验证码');
      return;
    }
    setActionLoading(true);
    setAuthError('');
    try {
      const result = await verifyAuthRecoverySMS(token, smsCode.trim());
      if (result.status === 'authenticated') {
        finishAuthenticated();
        return;
      }
      if (result.status === 'session_pending' && result.sms_verified) {
        setFlow(current => ({ ...current, ...result }));
        setSmsCode('');
      }
      if (result.status === 'captcha_invalid') {
        setFlow((current) => ({ ...current, ...result }));
        setCaptchaCode('');
        setSmsCode('');
        setSmsSent(false);
      }
      setAuthError(result.message || '短信验证码验证失败，请核对后重试');
    } catch (error) {
      handleActionError(error, '短信验证码验证失败，请重试');
    } finally {
      setActionLoading(false);
    }
  };

  const restart = async () => {
    start();
  };

  return (
    <main className="auth-recovery-page">
      <Card className="auth-recovery-card">
        <div className="auth-recovery-brand">
          <span>NEU</span>
          <SafetyCertificateOutlined />
        </div>
        <h1>恢复教务登录</h1>

        {stage === 'starting' && (
          <div className="auth-recovery-state">
            <Spin size="large" />
            <p>{message}</p>
          </div>
        )}

        {stage === 'qr_pending' && (
          <>
            <div className="auth-recovery-qr">
              <QRCode value={flow?.qr_content || ''} size={220} status="active" />
            </div>
            <div className="auth-recovery-countdown">
              <ClockCircleOutlined />
              本次二维码剩余 <strong>{formatCountdown(secondsLeft)}</strong>
            </div>
            <Alert type="info" showIcon message={message} />
          </>
        )}

        {stage === 'sms_required' && (
          <>
            <div className="auth-recovery-countdown is-sms">
              <ClockCircleOutlined />
              {secondsLeft > 0 ? (
                <>预计有效时间 <strong>{formatCountdown(secondsLeft)}</strong></>
              ) : (
                <strong>可重新发送或继续尝试验证</strong>
              )}
            </div>
            <WebVPNAuthFields
              embedded
              flow={flow}
              captchaCode={captchaCode}
              setCaptchaCode={setCaptchaCode}
              smsCode={smsCode}
              setSmsCode={setSmsCode}
              loading={actionLoading}
              captchaLoading={captchaLoading}
              smsSent={smsSent}
              error={authError}
              onRefreshCaptcha={refreshCaptcha}
              onSendSMS={sendSMS}
              onVerify={verifySMS}
            />
            <div className="auth-recovery-sms-actions">
              <Button onClick={restart} disabled={actionLoading || captchaLoading}>重新开始登录</Button>
              <Button
                type="primary"
                loading={actionLoading}
                disabled={!flow?.sms_verified && !smsCode.trim()}
                onClick={verifySMS}
              >
                {flow?.sms_verified ? '继续建立会话' : '验证并恢复登录'}
              </Button>
            </div>
          </>
        )}

        {stage === 'authenticated' && (
          <div className="auth-recovery-result is-success">
            <CheckCircleOutlined />
            <h2>登录已恢复</h2>
            <p>{message}</p>
            <small>此一次性链接现已失效，可以关闭页面。</small>
          </div>
        )}

        {['expired', 'error'].includes(stage) && (
          <div className="auth-recovery-result">
            <Alert type="warning" showIcon message={message} />
            <Button type="primary" icon={<ReloadOutlined />} onClick={restart}>
              重新开始登录
            </Button>
          </div>
        )}

        {stage === 'invalid' && (
          <Alert
            type="warning"
            showIcon
            message="链接已失效"
            description={message}
          />
        )}

        <p className="auth-recovery-footnote">
          一次性页面会连续完成扫码和学校要求的短信二次认证；验证码不会自动填写或发送。
          登录成功后链接立即失效，请勿转发当前页面地址。
        </p>
      </Card>
    </main>
  );
};

export default AuthRecoveryPage;
