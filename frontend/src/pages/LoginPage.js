import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Form, Input, Button, Checkbox, message, Spin, Radio, QRCode, Alert } from 'antd';
import {
  UserOutlined, LockOutlined, QrcodeOutlined, WechatOutlined,
  MobileOutlined, DownOutlined, DatabaseOutlined
} from '@ant-design/icons';
import {
  login, checkStatus, startWebVPNQRLogin, getWebVPNQRStatus, cancelWebVPNQRLogin,
  startWebVPNPasswordLogin, refreshWebVPNCaptcha, sendWebVPNSMSCode, verifyWebVPNSMSCode, cancelWebVPNSMSLogin,
  getOfflineStatus, getWebVPNErrorMessage, isWebVPNFlowInvalid, isWebVPNCampusNetworkBlocked,
} from '../services/api';
import './LoginPage.css';
import { isManualLogoutActive } from '../utils/authSessionPolicy';
import WebVPNAuthModal from '../components/WebVPNAuthModal';
import NativeServerSettingsButton from '../components/NativeServerSettingsButton';

const directFallbackCodes = new Set(['DIRECT_ACCESS_FAILED', 'WEBVPN_REQUIRED']);

const qrProviders = {
  smart_neu: {
    title: '智慧东大扫码',
    description: '打开智慧东大 App 扫一扫，并在手机上确认登录。',
    icon: MobileOutlined,
  },
  wechat: {
    title: '微信扫码',
    description: '打开微信扫一扫，并在手机上确认登录。',
    icon: WechatOutlined,
  },
};

const LoginPage = ({ onLoginSuccess, onOfflineSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [networkMode, setNetworkMode] = useState('direct');
  const [qrFlow, setQrFlow] = useState(null);
  const [qrProvider, setQrProvider] = useState(null);
  const [qrScanned, setQrScanned] = useState(false);
  const [qrMessage, setQrMessage] = useState('');
  const [qrLoading, setQrLoading] = useState(false);
  const qrCurrent = useRef(null);
  const qrPoll = useRef(null);
  const changingAuth = useRef(false);
  const passwordAttempt = useRef(null);
  const [smsFlow, setSmsFlow] = useState(null);
  const [captchaCode, setCaptchaCode] = useState('');
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [smsCode, setSmsCode] = useState('');
  const [smsLoading, setSmsLoading] = useState(false);
  const [smsSent, setSmsSent] = useState(false);
  const [offlineStatus, setOfflineStatus] = useState(null);
  const [offlineLoading, setOfflineLoading] = useState(false);
  const [networkNotice, setNetworkNotice] = useState('');
  const [switchingToWebVPN, setSwitchingToWebVPN] = useState(false);
  const [form] = Form.useForm();
  const normalizedCredentials = values => ({
    ...values,
    username: String(values?.username || '').trim(),
  });

  const handleCampusNetworkBlock = useCallback((value) => {
    const blocked = typeof isWebVPNCampusNetworkBlocked === 'function'
      && isWebVPNCampusNetworkBlocked(value);
    if (!blocked) return false;
    qrCurrent.current = null;
    setQrFlow(null);
    setQrProvider(null);
    setQrMessage('');
    setSmsFlow(null);
    setCaptchaCode('');
    setSmsCode('');
    setSmsSent(false);
    setNetworkMode('direct');
    setNetworkNotice(getWebVPNErrorMessage(
      value,
      '检测到校园网环境，学校 WebVPN 在校园网内不可用。已切换到校内直连，请确认后登录。',
    ));
    return true;
  }, []);

  const showSMSChallenge = useCallback(result => {
    setSmsFlow(result);
    setCaptchaCode('');
    setSmsCode('');
    setSmsSent(false);
  }, []);

  // 自然会话失效时允许再次静默恢复；用户明确退出后则只读取离线能力，
  // 避免 Cookie 或已保存凭据把用户立即带回主界面。
  useEffect(() => {
    const loadLoginState = async () => {
      try {
        const localStatus = await getOfflineStatus();
        setOfflineStatus(localStatus);
      } catch (error) {
        setOfflineStatus({
          available: false,
          has_scores: false,
          has_report: false,
          has_research: false,
          resources: [],
        });
      } finally {
        setChecking(false);
      }

      if (isManualLogoutActive()) return;
      try {
        const status = await checkStatus();
        handleCampusNetworkBlock(status);
        if (status.is_logged_in) {
          onLoginSuccess(status.current_user);
        }
      } catch (error) {
        console.log('检查登录状态失败', error);
      }
    };
    loadLoginState();
  }, [handleCampusNetworkBlock, onLoginSuccess]);

  useEffect(() => () => passwordAttempt.current?.abort(), []);

  useEffect(() => {
    if (!qrFlow) return undefined;
    const controller = new AbortController();
    qrPoll.current = controller;
    let stopped = false;
    let timer;
    const poll = async () => {
      try {
        const result = await getWebVPNQRStatus(qrFlow.flow_id, controller.signal);
        if (stopped || qrCurrent.current !== qrFlow) return;
        if (handleCampusNetworkBlock(result)) return;
        if (result.status === 'sms_required' && result.flow_id) {
          qrCurrent.current = null;
          setQrFlow(null);
          setQrProvider(null);
          showSMSChallenge(result);
        } else if (!result.success || ['expired', 'cancelled'].includes(result.status)) {
          setQrMessage(getWebVPNErrorMessage(result, '二维码登录失败'));
          setQrFlow(null);
        } else if (result.status === 'authenticated') {
          qrCurrent.current = null;
          setQrFlow(null);
          setQrProvider(null);
          onLoginSuccess(result.username);
        } else {
          if (result.status === 'scanned') setQrScanned(true);
          timer = window.setTimeout(poll, (qrFlow.poll_interval || 3) * 1000);
        }
      } catch (error) {
        if (stopped || qrCurrent.current !== qrFlow) return;
        if (handleCampusNetworkBlock(error)) return;
        setQrMessage(getWebVPNErrorMessage(error, '二维码状态检查失败，请重新获取'));
        setQrFlow(null);
      }
    };

    timer = window.setTimeout(poll, (qrFlow.poll_interval || 3) * 1000);
    return () => {
      stopped = true;
      controller.abort();
      window.clearTimeout(timer);
      if (qrPoll.current === controller) qrPoll.current = null;
    };
  }, [handleCampusNetworkBlock, onLoginSuccess, qrFlow, showSMSChallenge]);

  const cancelQRLogin = async () => {
    const flow = qrCurrent.current;
    qrCurrent.current = null;
    qrPoll.current?.abort();
    setQrFlow(null);
    setQrScanned(false);
    if (flow) await cancelWebVPNQRLogin(flow.flow_id).catch(() => {});
  };

  const changeQR = async provider => {
    if (changingAuth.current || smsFlow) return;
    changingAuth.current = true;
    setQrLoading(true);
    setQrProvider(provider);
    setQrMessage('');
    try {
      await cancelQRLogin();
      if (!provider) return;
      const result = await startWebVPNQRLogin(form.getFieldValue('username') || '', 'primary', provider, networkMode);
      if (handleCampusNetworkBlock(result)) return;
      if (!result.success || !result.qr_content || !result.flow_id) {
        setQrMessage(result.message || '无法启动二维码登录');
        return;
      }
      qrCurrent.current = result;
      setQrFlow(result);
    } catch (error) {
      if (handleCampusNetworkBlock(error)) return;
      setQrMessage(getWebVPNErrorMessage(error, '无法获取二维码，请稍后重试'));
    } finally {
      changingAuth.current = false;
      setQrLoading(false);
    }
  };

  const changeNetwork = nextMode => {
    if (changingAuth.current || smsFlow || nextMode === networkMode) return;
    setNetworkMode(nextMode);
    setNetworkNotice('');
    if (qrProvider) changeQR(null);
  };

  const beginWebVPNPasswordLogin = async (values, signal) => {
    try {
      const result = await startWebVPNPasswordLogin(values.username, values.password, values.remember, 'primary', signal);
      if (signal.aborted) return;
      if (handleCampusNetworkBlock(result)) return;
      if (result.status === 'sms_required' && result.flow_id) {
        showSMSChallenge(result);
      } else if (!result.success) {
        message.error(getWebVPNErrorMessage(result, 'WebVPN 登录失败'));
      } else if (result.status === 'authenticated') {
        onLoginSuccess(result.username || values.username);
      }
    } catch (error) {
      if (signal.aborted) return;
      if (handleCampusNetworkBlock(error)) return;
      message.error(getWebVPNErrorMessage(error, 'WebVPN 登录请求失败，请检查网络或使用扫码登录'));
    }
  };

  const sendSMSCode = async () => {
    if (!smsFlow || !captchaCode.trim()) {
      message.warning('请先填写图形验证码');
      return;
    }
    setSmsLoading(true);
    try {
      const result = await sendWebVPNSMSCode(smsFlow.flow_id, captchaCode.trim());
      if (handleCampusNetworkBlock(result)) return;
      if (!result.success && result.captcha_invalid) {
        setSmsFlow(prev => ({ ...prev, ...result }));
        setCaptchaCode('');
        setSmsCode('');
        setSmsSent(false);
        message.warning(getWebVPNErrorMessage(result, '图形验证码不正确，请核对新图片'));
      } else if (!result.success) {
        if (isWebVPNFlowInvalid(result)) {
          setSmsFlow(null);
          setCaptchaCode('');
          setSmsCode('');
        }
        message.error(getWebVPNErrorMessage(result, '短信验证码发送失败'));
      } else {
        setSmsSent(true);
        message.success('验证码已发送');
      }
    } catch (error) {
      if (handleCampusNetworkBlock(error)) return;
      message.error(getWebVPNErrorMessage(error, '短信验证码发送失败'));
    } finally {
      setSmsLoading(false);
    }
  };

  const refreshCaptcha = async () => {
    if (!smsFlow) return;
    setCaptchaLoading(true);
    try {
      const result = await refreshWebVPNCaptcha(smsFlow.flow_id);
      if (handleCampusNetworkBlock(result)) return;
      if (!result.success) throw Object.assign(new Error(getWebVPNErrorMessage(result, '刷新图形验证码失败')), { response: { data: result } });
      setSmsFlow(prev => ({ ...prev, ...result }));
      setCaptchaCode('');
      setSmsSent(false);
    } catch (error) {
      if (handleCampusNetworkBlock(error)) return;
      message.error(getWebVPNErrorMessage(error, '刷新图形验证码失败'));
      if (isWebVPNFlowInvalid(error)) setSmsFlow(null);
    } finally {
      setCaptchaLoading(false);
    }
  };

  const verifySMSCode = async () => {
    if (!smsFlow || (!smsFlow.sms_verified && !smsCode.trim())) {
      message.warning('请输入短信验证码');
      return;
    }
    setSmsLoading(true);
    try {
      const result = await verifyWebVPNSMSCode(smsFlow.flow_id, smsCode.trim());
      if (handleCampusNetworkBlock(result)) return;
      if (result.sms_verified && result.status === 'session_pending') {
        setSmsFlow(previous => ({ ...previous, ...result }));
        setSmsCode('');
        message.warning(result.message);
        return;
      }
      if (!result.success && result.status === 'captcha_invalid') {
        setSmsFlow(prev => ({ ...prev, ...result }));
        setCaptchaCode('');
        setSmsCode('');
        setSmsSent(false);
        message.warning(getWebVPNErrorMessage(result, '图形验证码不正确，请核对新图片'));
        return;
      }
      if (!result.success) {
        message.error(getWebVPNErrorMessage(result, '验证码验证失败'));
        if (isWebVPNFlowInvalid(result)) {
          setSmsFlow(null);
          setCaptchaCode('');
          setSmsCode('');
        }
        return;
      }
      setSmsFlow(null);
      onLoginSuccess(result.username || form.getFieldValue('username'));
    } catch (error) {
      if (handleCampusNetworkBlock(error)) return;
      message.error(getWebVPNErrorMessage(error, '验证码验证请求失败'));
      if (isWebVPNFlowInvalid(error)) setSmsFlow(null);
    } finally {
      setSmsLoading(false);
    }
  };

  const cancelSMSLogin = async () => {
    if (smsFlow) {
      await cancelWebVPNSMSLogin(smsFlow.flow_id).catch(() => {});
    }
    setSmsFlow(null);
    setCaptchaCode('');
    setSmsCode('');
  };

  const finishPasswordAttempt = () => {
    passwordAttempt.current = null;
    changingAuth.current = false;
    setLoading(false);
    setSwitchingToWebVPN(false);
  };

  const cancelPasswordLogin = () => {
    if (!passwordAttempt.current) return;
    passwordAttempt.current.abort();
    finishPasswordAttempt();
  };

  const onFinish = async (values) => {
    if (changingAuth.current || qrProvider || smsFlow) return;
    const credentials = normalizedCredentials(values);
    const controller = new AbortController();
    const { signal } = controller;
    passwordAttempt.current = controller;
    changingAuth.current = true;
    setNetworkNotice('');
    setLoading(true);
    try {
      if (networkMode === 'webvpn') {
        await beginWebVPNPasswordLogin(credentials, signal);
        return;
      }
      const result = await login(
        credentials.username,
        credentials.password,
        credentials.remember,
        'direct',
        signal,
      ).catch(error => {
        const result = error.response?.data;
        if (directFallbackCodes.has(result?.error_code)) return result;
        throw error;
      });
      if (signal.aborted) return;
      if (result.success) {
        onLoginSuccess(result.username);
      } else if (directFallbackCodes.has(result.error_code)) {
        setNetworkMode('webvpn');
        setSwitchingToWebVPN(true);
        await beginWebVPNPasswordLogin(credentials, signal);
      } else {
        message.error(result.message || result.suggestion || '登录失败');
      }
    } catch (error) {
      if (!signal.aborted) {
        message.error(getWebVPNErrorMessage(error, '登录请求失败，请检查服务端连接。'));
      }
    } finally {
      if (!signal.aborted && passwordAttempt.current === controller) finishPasswordAttempt();
    }
  };

  const beginOfflineMode = async () => {
    if (changingAuth.current || smsFlow) return;
    changingAuth.current = true;
    setOfflineLoading(true);
    try {
      await cancelQRLogin();
      setQrProvider(null);
      const status = await getOfflineStatus();
      setOfflineStatus(status);
      if (!status.available) {
        message.warning('本地还没有可离线查看的数据，请先在线登录并完成一次数据同步');
        return;
      }
      onOfflineSuccess(status);
    } catch (error) {
      message.error('无法读取本地离线数据');
    } finally {
      changingAuth.current = false;
      setOfflineLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="login-page">
        <div className="login-status" role="status" aria-live="polite">
          <Spin size="large" />
          <span>正在检查登录状态</span>
          <NativeServerSettingsButton />
        </div>
      </div>
    );
  }

  const busy = loading || qrLoading || offlineLoading || Boolean(smsFlow);
  const qrOption = qrProviders[qrProvider];
  const connectionStatus = switchingToWebVPN ? '直连不可用，正在通过 WebVPN 登录…'
    : networkMode === 'webvpn' ? '正在连接 WebVPN…' : '正在连接教务系统…';
  return (
    <div className="login-page">
      <main className="login-shell">
        <section className="login-brand" aria-label="东北大学教务系统工具箱">
          <div className="brand-mark">NEU</div>
          <div className="brand-rule" />
          <p className="brand-kicker">NORTHEASTERN UNIVERSITY</p>
          <h1>教务系统工具箱</h1>
          <p className="brand-description">统一管理成绩、培养计划、实验选课、考试与教学评价。</p>
          <div className="brand-footnote">校园服务访问</div>
        </section>

        <section className="login-panel" aria-labelledby="login-title">
          <header className="login-panel-header">
            <h2 id="login-title">登录教务服务</h2>
            <NativeServerSettingsButton />
          </header>

          <fieldset className="login-network">
            <legend>访问线路</legend>
            <Radio.Group
              aria-label="访问线路"
              className="access-mode-group"
              value={networkMode}
              onChange={event => changeNetwork(event.target.value)}
              optionType="button"
              buttonStyle="solid"
              disabled={busy}
            >
              <Radio.Button value="direct">校内直连</Radio.Button>
              <Radio.Button value="webvpn">WebVPN</Radio.Button>
            </Radio.Group>
          </fieldset>

          {networkNotice && (
            <Alert
              className="login-network-notice"
              type="warning"
              showIcon
              closable
              message="WebVPN 当前不可用"
              description={networkNotice}
              onClose={() => setNetworkNotice('')}
            />
          )}

          <div className={`login-fold${qrOption ? '' : ' is-open'}`} aria-hidden={Boolean(qrOption)}>
            <div className="login-fold-content">
              <Form
                form={form}
                name="neu-account-login"
                layout="vertical"
                initialValues={{ remember: true }}
                onFinish={onFinish}
                autoComplete="on"
                disabled={busy || Boolean(qrOption)}
              >
                <Form.Item
                  name="username"
                  label="学号"
                  rules={[{ required: true, message: '请输入学号' }]}
                >
                  <Input
                    prefix={<UserOutlined />}
                    placeholder="学号"
                    size="large"
                    inputMode="numeric"
                    autoComplete="section-neu-account username"
                    enterKeyHint="next"
                  />
                </Form.Item>

                <Form.Item
                  name="password"
                  label="密码"
                  rules={[{ required: true, message: '请输入密码' }]}
                >
                  <Input.Password
                    prefix={<LockOutlined />}
                    placeholder="密码"
                    size="large"
                    autoComplete="section-neu-account current-password"
                  />
                </Form.Item>

                <Form.Item name="remember" valuePropName="checked">
                  <Checkbox>记住密码（本地保存）</Checkbox>
                </Form.Item>
                <Form.Item>
                  <Button
                    className="login-password-action"
                    type={loading ? 'default' : 'primary'}
                    htmlType={loading ? 'button' : 'submit'}
                    disabled={(busy && !loading) || Boolean(qrOption)}
                    onClick={loading ? cancelPasswordLogin : undefined}
                    size="large"
                    block
                  >
                    {loading ? '取消登录' : '登录'}
                  </Button>
                  <div className="login-connection-status" role="status">{loading ? connectionStatus : ''}</div>
                </Form.Item>
              </Form>
            </div>
          </div>

          <section className="login-scan" aria-label="扫码登录">
            <div className="qr-method-grid">
              {Object.entries(qrProviders).map(([provider, { icon: Icon, title }]) => (
                <button
                  type="button"
                  key={provider}
                  className="qr-method-card"
                  aria-expanded={qrProvider === provider}
                  aria-controls="login-scan-panel"
                  disabled={busy}
                  onClick={() => changeQR(qrProvider === provider ? null : provider)}
                >
                  <Icon className="qr-method-icon" aria-hidden="true" />
                  <span>{title}</span>
                  <DownOutlined className="qr-method-arrow" aria-hidden="true" />
                </button>
              ))}
            </div>
            <div id="login-scan-panel" className={`login-fold${qrOption ? ' is-open' : ''}`} aria-hidden={!qrOption}>
              <div className="login-fold-content">
                {qrOption && (
                  <div className="qr-panel">
                    {qrLoading ? (
                      <div className="qr-loading" role="status"><Spin size="small" /> 正在获取二维码</div>
                    ) : qrFlow ? (
                      <>
                        <div className="qr-code-frame">
                          <QRCode type="svg" value={qrFlow.qr_content} size={176} color="#131b28" bgColor="#fff" aria-label={`${qrOption.title}二维码`} />
                        </div>
                        <div className={`qr-status${qrScanned ? ' is-scanned' : ''}`} role="status">
                          {qrScanned ? '已扫码，请在手机上确认' : '等待扫码'}
                        </div>
                        <p>{qrOption.description}</p>
                      </>
                    ) : (
                      <>
                        <Alert type="warning" showIcon message={qrMessage || '二维码已失效'} />
                        <Button icon={<QrcodeOutlined />} onClick={() => changeQR(qrProvider)}>重新获取</Button>
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          </section>
          {offlineStatus?.available && (
            <Button type="text" className="login-offline" icon={<DatabaseOutlined />} loading={offlineLoading}
              disabled={loading || qrLoading || Boolean(smsFlow)} onClick={beginOfflineMode}>
              离线查看
            </Button>
          )}
        </section>
      </main>
      <WebVPNAuthModal
        flow={smsFlow}
        captchaCode={captchaCode}
        setCaptchaCode={setCaptchaCode}
        smsCode={smsCode}
        setSmsCode={setSmsCode}
        loading={smsLoading}
        captchaLoading={captchaLoading}
        smsSent={smsSent}
        onRefreshCaptcha={refreshCaptcha}
        onSendSMS={sendSMSCode}
        onVerify={verifySMSCode}
        onCancel={cancelSMSLogin}
      />
    </div>
  );
};

export default LoginPage;
