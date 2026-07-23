import React, { useState, useEffect } from 'react';
import { Form, Input, Button, Checkbox, message, Spin, Radio, QRCode, Alert, Modal } from 'antd';
import { UserOutlined, LockOutlined, QrcodeOutlined, SafetyCertificateOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import {
  login, checkStatus, startWebVPNQRLogin, getWebVPNQRStatus, cancelWebVPNQRLogin,
  startWebVPNPasswordLogin, sendWebVPNSMSCode, verifyWebVPNSMSCode, cancelWebVPNSMSLogin,
} from '../services/api';
import './LoginPage.css';

const LoginPage = ({ onLoginSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [networkMode, setNetworkMode] = useState('direct');
  const [qrFlow, setQrFlow] = useState(null);
  const [loginView, setLoginView] = useState('password');
  const [qrMessage, setQrMessage] = useState('');
  const [qrSize, setQrSize] = useState(196);
  const [smsFlow, setSmsFlow] = useState(null);
  const [smsCode, setSmsCode] = useState('');
  const [smsLoading, setSmsLoading] = useState(false);
  const [smsSent, setSmsSent] = useState(false);
  const [form] = Form.useForm();

  // 检查登录状态
  useEffect(() => {
    const checkLoginStatus = async () => {
      try {
        const status = await checkStatus();
        // 已登录则直接进入
        if (status.is_logged_in) {
          onLoginSuccess(status.current_user);
        }
      } catch (error) {
        console.log('检查登录状态失败', error);
      } finally {
        setChecking(false);
      }
    };
    checkLoginStatus();
  }, [onLoginSuccess]);

  useEffect(() => {
    const updateQRSize = () => setQrSize(Math.max(144, Math.min(196, window.innerWidth - 96)));
    updateQRSize();
    window.addEventListener('resize', updateQRSize);
    return () => window.removeEventListener('resize', updateQRSize);
  }, []);

  useEffect(() => {
    if (!qrFlow) return undefined;

    const poll = async () => {
      try {
        const result = await getWebVPNQRStatus(qrFlow.flow_id);
        if (!result.success) {
          setQrMessage(result.message || '二维码登录失败');
          setQrFlow(null);
          return;
        }
        if (result.status === 'authenticated') {
          setQrFlow(null);
          onLoginSuccess(result.username || form.getFieldValue('username') || '已登录');
        } else if (result.status === 'expired') {
          setQrMessage('二维码已过期，请重新获取');
          setQrFlow(null);
        }
      } catch (error) {
        setQrMessage('二维码状态检查失败，请重新获取');
        setQrFlow(null);
      }
    };

    const timer = setInterval(poll, qrFlow.poll_interval * 1000);
    return () => clearInterval(timer);
  }, [form, onLoginSuccess, qrFlow]);

  const beginWebVPNQRLogin = async () => {
    setLoginView('qr');
    setLoading(true);
    setQrMessage('');
    try {
      const result = await startWebVPNQRLogin(form.getFieldValue('username') || '');
      if (!result.success) {
        setQrMessage(result.message || '无法启动二维码登录');
        return;
      }
      setNetworkMode('webvpn');
      setQrFlow(result);
    } catch (error) {
      setQrMessage('无法连接后端服务');
    } finally {
      setLoading(false);
    }
  };

  const cancelQRLogin = async () => {
    if (qrFlow) {
      await cancelWebVPNQRLogin(qrFlow.flow_id).catch(() => {});
    }
    setQrFlow(null);
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
        content: 'WebVPN 正在响应，可改用微信扫码快速登录。',
      });
    }, 5000);
    try {
      const result = await startWebVPNPasswordLogin(values.username, values.password, values.remember);
      if (!result.success) {
        message.error(result.message || 'WebVPN 登录失败');
      } else if (result.status === 'sms_required') {
        setSmsFlow(result);
        setSmsCode('');
        setSmsSent(false);
      } else if (result.status === 'authenticated') {
        onLoginSuccess(result.username || values.username);
      }
    } catch (error) {
      message.error('WebVPN 登录请求失败，请检查网络或使用微信扫码快速登录');
    } finally {
      clearTimeout(slowTimer);
      message.destroy(slowRequestKey);
    }
  };

  const sendSMSCode = async () => {
    if (!smsFlow) return;
    setSmsLoading(true);
    try {
      const result = await sendWebVPNSMSCode(smsFlow.flow_id);
      if (!result.success) {
        message.error(result.message || '短信验证码发送失败');
      } else {
        setSmsSent(true);
        message.success('验证码已发送');
      }
    } catch (error) {
      message.error('短信验证码发送失败');
    } finally {
      setSmsLoading(false);
    }
  };

  const verifySMSCode = async () => {
    if (!smsFlow || !smsCode.trim()) {
      message.warning('请输入短信验证码');
      return;
    }
    setSmsLoading(true);
    try {
      const result = await verifyWebVPNSMSCode(smsFlow.flow_id, smsCode.trim());
      if (!result.success) {
        message.error(result.message || '验证码验证失败');
        return;
      }
      setSmsFlow(null);
      onLoginSuccess(result.username || form.getFieldValue('username'));
    } catch (error) {
      message.error('验证码验证请求失败');
    } finally {
      setSmsLoading(false);
    }
  };

  const cancelSMSLogin = async () => {
    if (smsFlow) {
      await cancelWebVPNSMSLogin(smsFlow.flow_id).catch(() => {});
    }
    setSmsFlow(null);
    setSmsCode('');
  };

  const onFinish = async (values) => {
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
        await beginWebVPNPasswordLogin(values);
        return;
      }
      const result = await login(
        values.username,
        values.password,
        values.remember,
        networkMode,
      );
      
      if (result.success) {
        onLoginSuccess(result.username);
      } else if (result.requires_webvpn) {
        setNetworkMode('webvpn');
        message.warning(result.suggestion || '当前网络需要 WebVPN，请切换后继续登录。');
      } else {
        message.error(result.message || result.suggestion || '登录失败');
      }
    } catch (error) {
      message.error('登录请求失败: ' + error.message);
    } finally {
      if (slowTimer) clearTimeout(slowTimer);
      message.destroy(slowRequestKey);
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="login-page">
        <div className="login-status" role="status" aria-live="polite">
          <Spin size="large" />
          <span>正在检查登录状态</span>
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
            <p>校外网络可通过 WebVPN 安全访问。</p>
          </header>

        <div className="login-credentials-wrap" aria-hidden={loginView === 'qr'}>
          <Form
            form={form}
            name="login"
            initialValues={{ remember: true }}
            onFinish={onFinish}
            autoComplete="off"
          >
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入学号' }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder="学号"
              size="large"
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
            />
          </Form.Item>

          <Form.Item label="访问方式">
            <Radio.Group
              value={networkMode}
              onChange={(event) => setNetworkMode(event.target.value)}
              optionType="button"
              buttonStyle="solid"
              size="small"
            >
              <Radio.Button value="direct">校内直连</Radio.Button>
              <Radio.Button value="webvpn">WebVPN</Radio.Button>
            </Radio.Group>
          </Form.Item>

          <Form.Item name="remember" valuePropName="checked">
            <Checkbox>记住密码（本地保存）</Checkbox>
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              size="large"
              block
            >
              登录
            </Button>
          </Form.Item>
          </Form>
        </div>

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
                {qrFlow ? (
                  <>
                    <QRCode value={qrFlow.qr_content} size={qrSize} status="active" />
                    <div className="qr-caption">使用已关注东北大学微信企业号的微信扫码</div>
                    <Button type="link" onClick={cancelQRLogin}>取消本次认证</Button>
                  </>
                ) : (
                  <Button icon={<QrcodeOutlined />} loading={loading} block onClick={beginWebVPNQRLogin}>
                    获取微信登录二维码
                  </Button>
                )}
                <Button type="link" icon={<ArrowLeftOutlined />} onClick={showPasswordLogin}>
                  返回账号密码登录
                </Button>
              </div>
            ) : (
              <Button icon={<QrcodeOutlined />} loading={loading} block onClick={beginWebVPNQRLogin}>
                微信扫码快速登录
              </Button>
            )}
          </section>
        </section>
      </main>
      <Modal
        open={Boolean(smsFlow)}
        title="短信二次认证"
        okText="验证并登录"
        cancelText="取消"
        confirmLoading={smsLoading}
        onOk={verifySMSCode}
        onCancel={cancelSMSLogin}
        destroyOnHidden
      >
        <div className="sms-auth-content">
          <SafetyCertificateOutlined className="sms-auth-icon" />
          <p>账号密码已验证，请完成统一认证短信校验。</p>
          <Input
            value={smsCode}
            onChange={(event) => setSmsCode(event.target.value)}
            placeholder="请输入短信验证码"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={8}
            onPressEnter={verifySMSCode}
          />
          <Button type="link" loading={smsLoading} onClick={sendSMSCode}>
            {smsSent ? '重新发送验证码' : '发送验证码'}
          </Button>
          <p className="sms-auth-note">短信接收不便时，建议使用微信扫码快速登录。</p>
        </div>
      </Modal>
    </div>
  );
};

export default LoginPage;
