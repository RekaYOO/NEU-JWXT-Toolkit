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
  cancelGradeTrackingRecovery,
  getGradeTrackingRecoveryStatus,
  pollGradeTrackingRecovery,
  refreshGradeTrackingRecoveryCaptcha,
  sendGradeTrackingRecoverySMS,
  startGradeTrackingRecovery,
  verifyGradeTrackingRecoverySMS,
} from '../services/api';
import './GradeTrackingRecoveryPage.css';

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

const GradeTrackingRecoveryPage = ({ token }) => {
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

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const setCountdown = useCallback((value, fallback = 300) => {
    const next = Math.max(1, Number(value) || fallback);
    secondsLeftRef.current = next;
    setSecondsLeft(next);
  }, []);

  const enterSMSStage = useCallback((result) => {
    stopPolling();
    setFlow((current) => ({ ...current, ...result }));
    setCountdown(result.expires_in, 180);
    setCaptchaCode('');
    setSmsCode('');
    setSmsSent(false);
    setAuthError('');
    setStage('sms_required');
    setMessage('扫码已确认，请完成短信二次认证');
  }, [setCountdown, stopPolling]);

  const finishAuthenticated = useCallback(() => {
    stopPolling();
    setStage('authenticated');
    setMessage('教务登录已经恢复，成绩追踪将自动继续');
    setFlow(null);
  }, [stopPolling]);

  const poll = useCallback(async () => {
    try {
      const result = await pollGradeTrackingRecovery(token);
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
      if (error.response?.status === 404) {
        stopPolling();
        setStage('invalid');
        setMessage('一次性登录链接不存在或已经完成使用');
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
      const result = await startGradeTrackingRecovery(token);
      setFlow(result);
      setCountdown(result.expires_in, 300);
      setStage('qr_pending');
      setMessage('请使用微信扫码并确认登录');
      beginPolling(result.poll_interval);
    } catch (error) {
      setStage(error.response?.status === 404 ? 'invalid' : 'error');
      setMessage(recoveryErrorMessage(error, '暂时无法创建二维码，请稍后重试'));
    }
  }, [beginPolling, setCountdown, stopPolling, token]);

  const restore = useCallback(async () => {
    try {
      const result = await getGradeTrackingRecoveryStatus(token);
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
      const nextValue = Math.max(0, secondsLeftRef.current - 1);
      secondsLeftRef.current = nextValue;
      setSecondsLeft(nextValue);
      if (nextValue === 0) {
        stopPolling();
        setStage('expired');
        setMessage(
          stage === 'sms_required'
            ? '本次短信验证已失效，请重新开始登录'
            : '本次二维码已失效，请重新开始登录'
        );
      }
    }, 1000);
    return () => window.clearInterval(countdown);
  }, [stage, stopPolling]);

  const refreshCaptcha = async () => {
    setCaptchaLoading(true);
    setAuthError('');
    try {
      const result = await refreshGradeTrackingRecoveryCaptcha(token);
      setFlow((current) => ({ ...current, ...result }));
      setCaptchaCode('');
      setSmsCode('');
      setSmsSent(false);
    } catch (error) {
      setAuthError(recoveryErrorMessage(error, '刷新图形验证码失败，请重试'));
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
      const result = await sendGradeTrackingRecoverySMS(token, captchaCode.trim());
      if (result.status === 'captcha_invalid') {
        setFlow((current) => ({ ...current, ...result }));
        setCaptchaCode('');
        setSmsCode('');
        setSmsSent(false);
        setAuthError(result.message || '图形验证码不正确，请重新填写');
        return;
      }
      setSmsSent(true);
      setMessage('短信验证码已发送，请查收并填写');
    } catch (error) {
      setAuthError(recoveryErrorMessage(error, '短信验证码发送失败，请重试'));
    } finally {
      setActionLoading(false);
    }
  };

  const verifySMS = async () => {
    if (!smsCode.trim()) {
      setAuthError('请输入短信验证码');
      return;
    }
    setActionLoading(true);
    setAuthError('');
    try {
      const result = await verifyGradeTrackingRecoverySMS(token, smsCode.trim());
      if (result.status === 'authenticated') {
        finishAuthenticated();
        return;
      }
      if (result.status === 'captcha_invalid') {
        setFlow((current) => ({ ...current, ...result }));
        setCaptchaCode('');
        setSmsCode('');
        setSmsSent(false);
      }
      setAuthError(result.message || '短信验证码验证失败，请核对后重试');
    } catch (error) {
      setAuthError(recoveryErrorMessage(error, '短信验证码验证失败，请重试'));
    } finally {
      setActionLoading(false);
    }
  };

  const restart = async () => {
    try {
      await cancelGradeTrackingRecovery(token);
    } catch (error) {
      if (error.response?.status === 404) {
        setStage('invalid');
        setMessage('一次性登录链接不存在或已经完成使用');
        return;
      }
    }
    start();
  };

  return (
    <main className="tracking-recovery-page">
      <Card className="tracking-recovery-card">
        <div className="tracking-recovery-brand">
          <span>NEU</span>
          <SafetyCertificateOutlined />
        </div>
        <h1>恢复成绩追踪登录</h1>

        {stage === 'starting' && (
          <div className="tracking-recovery-state">
            <Spin size="large" />
            <p>{message}</p>
          </div>
        )}

        {stage === 'qr_pending' && (
          <>
            <div className="tracking-recovery-qr">
              <QRCode value={flow?.qr_content || ''} size={220} status="active" />
            </div>
            <div className="tracking-recovery-countdown">
              <ClockCircleOutlined />
              本次二维码剩余 <strong>{formatCountdown(secondsLeft)}</strong>
            </div>
            <Alert type="info" showIcon message={message} />
          </>
        )}

        {stage === 'sms_required' && (
          <>
            <div className="tracking-recovery-countdown is-sms">
              <ClockCircleOutlined />
              本次验证剩余 <strong>{formatCountdown(secondsLeft)}</strong>
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
            <div className="tracking-recovery-sms-actions">
              <Button onClick={restart}>重新开始登录</Button>
              <Button
                type="primary"
                loading={actionLoading}
                disabled={!smsCode.trim()}
                onClick={verifySMS}
              >
                验证并恢复登录
              </Button>
            </div>
          </>
        )}

        {stage === 'authenticated' && (
          <div className="tracking-recovery-result is-success">
            <CheckCircleOutlined />
            <h2>登录已恢复</h2>
            <p>{message}</p>
            <small>此一次性链接现已失效，可以关闭页面。</small>
          </div>
        )}

        {['expired', 'error'].includes(stage) && (
          <div className="tracking-recovery-result">
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

        <p className="tracking-recovery-footnote">
          一次性页面会连续完成扫码和学校要求的短信二次认证；验证码不会自动填写或发送。
          登录成功后链接立即失效，请勿转发当前页面地址。
        </p>
      </Card>
    </main>
  );
};

export default GradeTrackingRecoveryPage;
