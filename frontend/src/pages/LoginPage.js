import React, { useState, useEffect, useCallback } from 'react';
import { Form, Input, Button, Checkbox, message, Spin, Radio, QRCode, Alert } from 'antd';
import {
  UserOutlined, LockOutlined, QrcodeOutlined, WechatOutlined,
  MobileOutlined, SafetyCertificateOutlined, CheckCircleFilled,
  ArrowLeftOutlined, DatabaseOutlined
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

const qrProviders = {
  smart_neu: {
    title: '智慧东大扫码',
    shortTitle: '智慧东大',
    description: '使用智慧东大 App 扫码确认',
    icon: MobileOutlined,
    steps: ['打开智慧东大 App', '扫一扫当前二维码', '在 App 内确认登录'],
  },
  wechat: {
    title: '微信扫码',
    shortTitle: '微信',
    description: '使用东北大学企业微信扫码',
    icon: WechatOutlined,
    steps: ['打开微信扫一扫', '扫描当前二维码', '在微信内确认登录'],
  },
};

const LoginPage = ({ onLoginSuccess, onOfflineSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [networkMode, setNetworkMode] = useState('direct');
  const [qrFlow, setQrFlow] = useState(null);
  const [qrProvider, setQrProvider] = useState('smart_neu');
  const [qrScanned, setQrScanned] = useState(false);
  const [loginView, setLoginView] = useState('password');
  const [qrMessage, setQrMessage] = useState('');
  const [qrSize, setQrSize] = useState(196);
  const [smsFlow, setSmsFlow] = useState(null);
  const [captchaCode, setCaptchaCode] = useState('');
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [smsCode, setSmsCode] = useState('');
  const [smsLoading, setSmsLoading] = useState(false);
  const [smsSent, setSmsSent] = useState(false);
  const [offlineStatus, setOfflineStatus] = useState(null);
  const [offlineLoading, setOfflineLoading] = useState(false);
  const [networkNotice, setNetworkNotice] = useState('');
  const [form] = Form.useForm();
  const normalizedCredentials = values => ({
    ...values,
    username: String(values?.username || '').trim(),
  });

  const handleCampusNetworkBlock = useCallback((value) => {
    const blocked = typeof isWebVPNCampusNetworkBlocked === 'function'
      && isWebVPNCampusNetworkBlocked(value);
    if (!blocked) return false;
    setQrFlow(null);
    setSmsFlow(null);
    setCaptchaCode('');
    setSmsCode('');
    setSmsSent(false);
    setLoginView('password');
    setNetworkMode('direct');
    setNetworkNotice(getWebVPNErrorMessage(
      value,
      '检测到校园网环境，学校 WebVPN 在校园网内不可用。已切换到校内直连，请确认后登录。',
    ));
    return true;
  }, []);

  const handleDirectWebVPNRequired = useCallback((value) => {
    const payload = value?.response?.data || value;
    if (!payload?.requires_webvpn) return false;
    setNetworkMode('webvpn');
    message.warning(
      payload.suggestion
      || payload.message
      || '当前网络无法直连教务系统，请切换到 WebVPN 后继续登录。',
    );
    return true;
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

  useEffect(() => {
    const updateQRSize = () => setQrSize(Math.max(144, Math.min(196, window.innerWidth - 96)));
    updateQRSize();
    window.addEventListener('resize', updateQRSize);
    return () => window.removeEventListener('resize', updateQRSize);
  }, []);

  useEffect(() => {
    if (!qrFlow) return undefined;
    let stopped = false;
    let timer;
    const poll = async () => {
      try {
        const result = await getWebVPNQRStatus(qrFlow.flow_id);
        if (stopped) return;
        if (handleCampusNetworkBlock(result)) return;
        if (!result.success) {
          setQrMessage(getWebVPNErrorMessage(result, '二维码登录失败'));
          setQrFlow(null);
          return;
        }
        if (result.status === 'authenticated') {
          setQrFlow(null);
          onLoginSuccess(result.username || form.getFieldValue('username'));
        } else if (result.status === 'sms_required') {
          // QR login and password login share the official second-auth form.
          // Keep the candidate Session alive and hand the same flow to the
          // shared CAPTCHA/SMS modal instead of treating it as a QR failure.
          setQrFlow(null);
          setSmsFlow(result);
          setCaptchaCode('');
          setSmsCode('');
          setSmsSent(false);
        } else if (['expired', 'cancelled'].includes(result.status)) {
          setQrMessage(result.message || '二维码已失效，请重新获取');
          setQrFlow(null);
        } else {
          if (result.status === 'scanned') setQrScanned(true);
          timer = window.setTimeout(poll, (qrFlow.poll_interval || 3) * 1000);
        }
      } catch (error) {
        if (stopped) return;
        if (handleCampusNetworkBlock(error)) return;
        setQrMessage(getWebVPNErrorMessage(error, '二维码状态检查失败，请重新获取'));
        setQrFlow(null);
      }
    };

    timer = window.setTimeout(poll, (qrFlow.poll_interval || 3) * 1000);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [form, handleCampusNetworkBlock, onLoginSuccess, qrFlow]);

  const beginWebVPNQRLogin = async (provider) => {
    setLoginView('qr');
    setQrProvider(provider);
    setQrScanned(false);
    setLoading(true);
    setQrMessage('');
    try {
      const result = await startWebVPNQRLogin(form.getFieldValue('username') || '', 'primary', provider);
      if (handleCampusNetworkBlock(result)) return;
      if (!result.success) {
        setQrMessage(result.message || '无法启动二维码登录');
        return;
      }
      setNetworkMode('webvpn');
      setQrFlow(result);
    } catch (error) {
      if (handleCampusNetworkBlock(error)) return;
      setQrMessage('无法连接后端服务');
    } finally {
      setLoading(false);
    }
  };

  const cancelQRLogin = async () => {
    setQrFlow(null);
    if (qrFlow) {
      await cancelWebVPNQRLogin(qrFlow.flow_id).catch(() => {});
    }
  };

  const showPasswordLogin = async () => {
    await cancelQRLogin();
    setQrMessage('');
    setLoginView('password');
  };

  const beginWebVPNPasswordLogin = async (values) => {
    const slowRequestKey = 'webvpn-password-slow';
    const slowTimer = setTimeout(() => {
      message.warning({
        key: slowRequestKey,
        duration: 0,
        content: 'WebVPN 正在响应，可改用扫码登录。',
      });
    }, 5000);
    try {
      const result = await startWebVPNPasswordLogin(values.username, values.password, values.remember);
      if (handleCampusNetworkBlock(result)) return;
      if (!result.success) {
        message.error(getWebVPNErrorMessage(result, 'WebVPN 登录失败'));
      } else if (result.status === 'sms_required') {
        setSmsFlow(result);
        setCaptchaCode('');
        setSmsCode('');
        setSmsSent(false);
      } else if (result.status === 'authenticated') {
        onLoginSuccess(result.username || values.username);
      }
    } catch (error) {
      if (handleCampusNetworkBlock(error)) return;
      message.error(getWebVPNErrorMessage(error, 'WebVPN 登录请求失败，请检查网络或使用扫码登录'));
    } finally {
      clearTimeout(slowTimer);
      message.destroy(slowRequestKey);
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

  const onFinish = async (values) => {
    const credentials = normalizedCredentials(values);
    if (networkMode === 'offline') {
      await beginOfflineMode();
      return;
    }

    setLoading(true);
    const slowRequestKey = 'direct-login-slow';
    const slowTimer = networkMode === 'direct' ? setTimeout(() => {
      message.warning({
        key: slowRequestKey,
        duration: 0,
        content: '直连响应较慢，请检查校园网络；校外可切换 WebVPN。',
      });
    }, 5000) : null;
    try {
      if (networkMode === 'webvpn') {
        await beginWebVPNPasswordLogin(credentials);
        return;
      }
      const result = await login(
        credentials.username,
        credentials.password,
        credentials.remember,
        networkMode,
      );
      
      if (result.success) {
        onLoginSuccess(result.username);
      } else if (handleDirectWebVPNRequired(result)) {
        return;
      } else {
        message.error(result.message || result.suggestion || '登录失败');
      }
    } catch (error) {
      if (!handleDirectWebVPNRequired(error)) {
        message.error(getWebVPNErrorMessage(error, '登录请求失败，请检查服务端连接。'));
      }
    } finally {
      if (slowTimer) clearTimeout(slowTimer);
      message.destroy(slowRequestKey);
      setLoading(false);
    }
  };

  const beginOfflineMode = async () => {
    setOfflineLoading(true);
    try {
      const status = await getOfflineStatus();
      setOfflineStatus(status);
      if (!status.available) {
        setNetworkMode('direct');
        message.warning('本地还没有可离线查看的数据，请先在线登录并完成一次数据同步');
        return;
      }
      onOfflineSuccess(status);
    } catch (error) {
      setNetworkMode('direct');
      message.error('无法读取本地离线数据');
    } finally {
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

  return (
    <div className="login-page">
      <main className={`login-shell${loginView === 'qr' ? ' is-qr-active' : ''}`}>
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
            <p className="panel-kicker">账户认证</p>
            <h2 id="login-title">登录教务服务</h2>
            <p>
              {networkMode === 'offline'
                ? '使用当前设备已保存的数据，不连接教务系统。'
                : '校外网络可通过 WebVPN 安全访问。'}
            </p>
            <NativeServerSettingsButton />
          </header>

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

        <div className="login-credentials-wrap" aria-hidden={loginView === 'qr'}>
          <Form
            form={form}
            name="neu-account-login"
            initialValues={{ remember: true }}
            onFinish={onFinish}
            autoComplete="on"
          >
          <Form.Item label="访问方式">
            <Radio.Group
              className="access-mode-group"
              value={networkMode}
              onChange={(event) => {
                setNetworkMode(event.target.value);
                setNetworkNotice('');
              }}
              optionType="button"
              buttonStyle="solid"
              size="small"
            >
              <Radio.Button value="direct">校内直连</Radio.Button>
              <Radio.Button value="webvpn">WebVPN</Radio.Button>
              {offlineStatus?.available && (
                <Radio.Button value="offline">离线查看</Radio.Button>
              )}
            </Radio.Group>
          </Form.Item>

          {networkMode === 'offline' ? (
            <div className="offline-mode-note">
              <DatabaseOutlined />
              <span>只读查看当前设备已有缓存的功能页</span>
            </div>
          ) : (
            <>
              <Form.Item
                name="username"
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
            </>
          )}

          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              loading={networkMode === 'offline' ? offlineLoading : loading}
              size="large"
              block
            >
              {networkMode === 'offline' ? '进入离线模式' : '登录'}
            </Button>
          </Form.Item>
          </Form>
        </div>

          {networkMode !== 'offline' && (
          <section className="webvpn-section" aria-labelledby="webvpn-title">
            <div className="webvpn-section-heading">
              <div>
                <p className="panel-kicker">校外访问</p>
                <h3 id="webvpn-title">WebVPN 校外访问</h3>
              </div>
              {loginView === 'password' && <QrcodeOutlined className="webvpn-icon" />}
            </div>
            {qrMessage && <Alert type="warning" showIcon message={qrMessage} style={{ marginBottom: 16 }} />}
            {loginView === 'qr' ? (
              <div className="webvpn-qr-login">
                <div className="qr-flow-toolbar">
                  <Button type="text" size="small" icon={<ArrowLeftOutlined />} disabled={loading} onClick={showPasswordLogin}>
                    账号密码登录
                  </Button>
                  <span className="qr-provider-pill">
                    {React.createElement(qrProviders[qrProvider].icon)}
                    {qrProviders[qrProvider].shortTitle}
                  </span>
                </div>
                {qrFlow ? (
                  <div className="qr-active-layout">
                    <div className="qr-code-frame">
                      <QRCode value={qrFlow.qr_content} size={qrSize} status="active" />
                      <span className="qr-code-corner qr-code-corner-tl" aria-hidden="true" />
                      <span className="qr-code-corner qr-code-corner-tr" aria-hidden="true" />
                      <span className="qr-code-corner qr-code-corner-bl" aria-hidden="true" />
                      <span className="qr-code-corner qr-code-corner-br" aria-hidden="true" />
                    </div>
                    <div className="qr-flow-copy">
                      <div className={`qr-status-chip${qrScanned ? ' is-scanned' : ''}`} role="status" aria-live="polite">
                        <span className="qr-status-dot" aria-hidden="true" />
                        {qrScanned ? '已扫码，等待确认' : '等待扫码'}
                      </div>
                      <h4>{qrProviders[qrProvider].title}</h4>
                      <p className="qr-caption">{qrScanned ? '请在手机上确认本次登录，页面会自动继续。' : qrProviders[qrProvider].description}</p>
                      <ol className="qr-step-list">
                        {qrProviders[qrProvider].steps.map((step, index) => (
                          <li key={step} className={qrScanned && index < 2 ? 'is-done' : ''}>
                            <span>{qrScanned && index < 2 ? <CheckCircleFilled /> : index + 1}</span>
                            {step}
                          </li>
                        ))}
                      </ol>
                      <Button type="link" className="qr-cancel-button" onClick={cancelQRLogin}>取消本次认证</Button>
                    </div>
                  </div>
                ) : (
                  <div className="qr-method-picker">
                    <p className="qr-picker-intro">选择你手机上方便使用的扫码方式</p>
                    <div className="qr-method-grid" role="list" aria-label="扫码方式">
                      {Object.entries(qrProviders).map(([provider, option]) => {
                        const Icon = option.icon;
                        const selected = qrProvider === provider;
                        return (
                          <button
                            type="button"
                            key={provider}
                            className={`qr-method-card${selected ? ' is-selected' : ''}`}
                            aria-pressed={selected}
                            onClick={() => setQrProvider(provider)}
                          >
                            <span className="qr-method-icon"><Icon /></span>
                            <span className="qr-method-card-copy">
                              <strong>{option.title}</strong>
                              <small>{option.description}</small>
                            </span>
                            <span className="qr-method-check" aria-hidden="true">{selected ? <CheckCircleFilled /> : ''}</span>
                          </button>
                        );
                      })}
                    </div>
                    <Button className="qr-start-button" type="primary" icon={<QrcodeOutlined />} loading={loading} block onClick={() => beginWebVPNQRLogin(qrProvider)}>
                      获取二维码
                    </Button>
                    <p className="qr-security-note"><SafetyCertificateOutlined /> 二维码一次有效，登录确认由学校认证系统完成</p>
                  </div>
                )}
              </div>
            ) : (
              <div className="webvpn-qr-login">
                <div className="qr-method-grid" role="list" aria-label="扫码方式">
                  {Object.entries(qrProviders).map(([provider, option]) => {
                    const Icon = option.icon;
                    return (
                      <button
                        type="button"
                        key={provider}
                        className="qr-method-card"
                        onClick={() => { setQrProvider(provider); beginWebVPNQRLogin(provider); }}
                      >
                        <span className="qr-method-icon"><Icon /></span>
                        <span className="qr-method-card-copy">
                          <strong>{option.title}</strong>
                          <small>{option.description}</small>
                        </span>
                        <QrcodeOutlined className="qr-method-arrow" aria-hidden="true" />
                      </button>
                    );
                  })}
                </div>
                <p className="qr-security-note"><SafetyCertificateOutlined /> 仅通过学校认证系统建立会话，不保存扫码内容</p>
              </div>
            )}
          </section>
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
