import React, { useState } from 'react';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { LockOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { loginAccessGateway } from '../services/api';
import './AccessLoginPage.css';

const { Text, Title } = Typography;

const AccessLoginPage = ({ configured, onSuccess }) => {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async ({ password }) => {
    setSubmitting(true);
    setError('');
    try {
      await loginAccessGateway(password);
      await onSuccess();
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '验证失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="access-login-page">
      <Card className="access-login-card">
        <div className="access-login-brand">
          <span>NEU</span>
          <SafetyCertificateOutlined />
        </div>
        <Title level={2}>访问教务工具箱</Title>
        <Text type="secondary">
          这是一个私有部署实例，请先验证服务器访问密码。
        </Text>

        {!configured ? (
          <Alert
            className="access-login-alert"
            type="warning"
            showIcon
            message="服务器尚未完成初始化"
            description="请在服务器上重新运行安装脚本并设置访问密码。"
          />
        ) : (
          <Form layout="vertical" onFinish={handleSubmit} className="access-login-form">
            <Form.Item
              label="访问密码"
              name="password"
              rules={[{ required: true, message: '请输入访问密码' }]}
            >
              <Input.Password
                autoFocus
                autoComplete="current-password"
                prefix={<LockOutlined />}
                placeholder="输入服务器访问密码"
                size="large"
              />
            </Form.Item>
            {error && (
              <Alert
                className="access-login-alert"
                type="error"
                showIcon
                message={error}
              />
            )}
            <Button type="primary" htmlType="submit" loading={submitting} block size="large">
              进入工具箱
            </Button>
          </Form>
        )}
      </Card>
    </main>
  );
};

export default AccessLoginPage;
