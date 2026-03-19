import React, { useState, useEffect } from 'react';
import { Card, Form, Input, Button, Checkbox, message, Spin } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { login, checkStatus } from '../services/api';
import './LoginPage.css';

const LoginPage = ({ onLoginSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);

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
  }, []);

  const onFinish = async (values) => {
    setLoading(true);
    try {
      const result = await login(
        values.username,
        values.password,
        values.remember
      );
      
      if (result.success) {
        onLoginSuccess(result.username);
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

        <div className="login-tips">
          <p>提示：</p>
          <ul>
            <li>首次登录需要联网验证</li>
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
