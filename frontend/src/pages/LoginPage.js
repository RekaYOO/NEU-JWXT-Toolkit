import React, { useState, useEffect } from 'react';
import { Card, Form, Input, Button, Checkbox, message, Spin, Radio, Divider, QRCode, Alert } from 'antd';
import { UserOutlined, LockOutlined, QrcodeOutlined } from '@ant-design/icons';
import { login, checkStatus, startWebVPNQRLogin, getWebVPNQRStatus, cancelWebVPNQRLogin } from '../services/api';
import './LoginPage.css';

const LoginPage = ({ onLoginSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [networkMode, setNetworkMode] = useState('auto');
  const [qrFlow, setQrFlow] = useState(null);
  const [qrMessage, setQrMessage] = useState('');
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

  const onFinish = async (values) => {
    setLoading(true);
    try {
      const result = await login(
        values.username,
        values.password,
        values.remember,
        networkMode,
      );
      
      if (result.success) {
        onLoginSuccess(result.username);
      } else if (result.requires_webvpn) {
        message.info('当前网络需要 WebVPN，请使用二维码登录');
        await beginWebVPNQRLogin();
      } else {
        message.error(result.message || '登录失败');
      }
    } catch (error) {
      message.error('登录请求失败: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="login-page">
        <Spin size="large" tip="检查登录状态..." />
      </div>
    );
  }

  return (
    <div className="login-page">
      <Card className="login-card" title="NEU教务系统工具箱">
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

          <Form.Item label="访问方式">
            <Radio.Group
              value={networkMode}
              onChange={(event) => setNetworkMode(event.target.value)}
              optionType="button"
              buttonStyle="solid"
              size="small"
            >
              <Radio.Button value="auto">自动</Radio.Button>
              <Radio.Button value="direct">校内直连</Radio.Button>
              <Radio.Button value="webvpn">WebVPN</Radio.Button>
            </Radio.Group>
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

        <Divider plain>校外访问</Divider>
        {qrMessage && <Alert type="warning" showIcon message={qrMessage} style={{ marginBottom: 16 }} />}
        {qrFlow ? (
          <div className="webvpn-qr-login">
            <QRCode value={qrFlow.qr_content} size={196} status="active" />
            <div className="qr-caption">使用已关注东北大学微信企业号的微信扫码</div>
            <Button type="link" onClick={cancelQRLogin}>取消</Button>
          </div>
        ) : (
          <Button icon={<QrcodeOutlined />} loading={loading} block onClick={beginWebVPNQRLogin}>
            WebVPN 二维码登录
          </Button>
        )}

        <div className="login-tips">
          <p>提示：</p>
          <ul>
            <li>校外网络会自动切换至 WebVPN</li>
            <li>勾选"记住密码"可自动登录</li>
            <li>成绩数据会自动保存到本地</li>
            <li>本地数据3天内有效，过期自动从云端更新</li>
          </ul>
        </div>
      </Card>
    </div>
  );
};

export default LoginPage;
