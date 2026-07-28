import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  message,
} from 'antd';
import {
  BellOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  MailOutlined,
  ReloadOutlined,
  SaveOutlined,
} from '@ant-design/icons';
import {
  checkGradesNow,
  getGradeTrackingConfig,
  getGradeTrackingStatus,
  setGradeTrackingEnabled,
  testGradeTrackingEmail,
  updateGradeTrackingConfig,
} from '../services/api';
import './GradeTrackingPage.css';

const STAGES = {
  disabled: { color: 'default', label: '未启用' },
  scheduled: { color: 'processing', label: '等待检查' },
  checking: { color: 'processing', label: '正在检查' },
  monitoring: { color: 'success', label: '运行中' },
  outside_window: { color: 'warning', label: '时段外' },
  waiting_login: { color: 'warning', label: '等待登录' },
  waiting_qr: { color: 'processing', label: '等待确认' },
  error: { color: 'error', label: '检查异常' },
};

const hourOptions = Array.from({ length: 24 }, (_, hour) => ({
  value: hour,
  label: `${String(hour).padStart(2, '0')}:00`,
}));

const endHourOptions = Array.from({ length: 24 }, (_, index) => {
  const hour = index + 1;
  return {
    value: hour,
    label: hour === 24 ? '24:00' : `${String(hour).padStart(2, '0')}:00`,
  };
});

const errorText = (error, fallback) => error.response?.data?.detail || fallback;

const formatTime = (value) => {
  if (!value) return '尚无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};

const GradeTrackingPage = () => {
  const [form] = Form.useForm();
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [checking, setChecking] = useState(false);
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState({ stage: 'disabled', enabled: false });
  const [passwordConfigured, setPasswordConfigured] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const currentStatus = await getGradeTrackingStatus();
      setStatus(currentStatus);
      setEnabled(Boolean(currentStatus.enabled));
    } catch (error) {
      // 页面初始化会单独提示；轮询失败时保持上一次可用状态。
    }
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const [config, currentStatus] = await Promise.all([
          getGradeTrackingConfig(),
          getGradeTrackingStatus(),
        ]);
        const {
          smtp_password_configured: hasPassword,
          enabled: configuredEnabled,
          ...fields
        } = config;
        setPasswordConfigured(hasPassword);
        setEnabled(configuredEnabled);
        form.setFieldsValue(fields);
        setStatus(currentStatus);
      } catch (error) {
        message.error(errorText(error, '成绩追踪配置加载失败'));
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [form]);

  useEffect(() => {
    const timer = window.setInterval(loadStatus, 15000);
    return () => window.clearInterval(timer);
  }, [loadStatus]);

  const saveConfig = async () => {
    try {
      const values = { ...await form.validateFields(), enabled };
      setSaving(true);
      const result = await updateGradeTrackingConfig(values);
      const {
        smtp_password_configured: hasPassword,
        enabled: configuredEnabled,
        ...fields
      } = result.config;
      setPasswordConfigured(hasPassword);
      setEnabled(configuredEnabled);
      form.setFieldsValue({ ...fields, smtp_password: undefined });
      await loadStatus();
      message.success('配置已保存');
    } catch (error) {
      if (!error.errorFields) {
        message.error(errorText(error, '保存配置失败'));
      }
    } finally {
      setSaving(false);
    }
  };

  const toggleTracking = async (nextEnabled) => {
    setToggling(true);
    try {
      const result = await setGradeTrackingEnabled(nextEnabled);
      setEnabled(Boolean(result.config.enabled));
      await loadStatus();
      message.success(nextEnabled ? '成绩追踪已开启' : '成绩追踪已关闭');
    } catch (error) {
      message.error(errorText(
        error,
        nextEnabled ? '开启失败，请先保存完整配置' : '关闭成绩追踪失败'
      ));
    } finally {
      setToggling(false);
    }
  };

  const checkNow = async () => {
    setChecking(true);
    try {
      await checkGradesNow();
      await loadStatus();
      message.success('成绩检查已完成');
    } catch (error) {
      message.error(errorText(error, '成绩检查失败，请确认教务登录状态'));
    } finally {
      setChecking(false);
    }
  };

  const testEmail = async () => {
    setTesting(true);
    try {
      await testGradeTrackingEmail();
      message.success('测试邮件已发送');
    } catch (error) {
      message.error(errorText(error, '测试邮件发送失败，请先保存配置'));
    } finally {
      setTesting(false);
    }
  };

  const stage = STAGES[status.stage] || STAGES.disabled;

  if (loading) {
    return (
      <div className="tracking-loading">
        <Spin />
        <span>正在读取追踪配置</span>
      </div>
    );
  }

  return (
    <div className="tracking-page">
      <section className="tracking-hero">
        <div className="tracking-heading">
          <span className="tracking-heading-icon"><BellOutlined /></span>
          <div>
            <h1>成绩追踪</h1>
            <p>定时检查成绩变化，并通过邮件提醒你。</p>
          </div>
        </div>
        <Switch
          checked={Boolean(enabled)}
          checkedChildren="已开启"
          unCheckedChildren="未开启"
          loading={toggling}
          disabled={saving}
          onChange={toggleTracking}
        />
      </section>

      <Card className="tracking-status-card">
        <div className="tracking-status-main">
          <div>
            <Tag color={stage.color}>{stage.label}</Tag>
            <strong>{status.message || '追踪状态尚未更新'}</strong>
          </div>
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={checking} onClick={checkNow}>
              立即检查
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              disabled={toggling}
              onClick={saveConfig}
            >
              保存配置
            </Button>
          </Space>
        </div>
        <div className="tracking-status-meta">
          <span><CheckCircleOutlined /> 上次成功：{formatTime(status.last_success_at)}</span>
          <span><ClockCircleOutlined /> 下次检查：{formatTime(status.next_check_at)}</span>
          {status.pending_notifications > 0 && (
            <span><MailOutlined /> 待发送通知：{status.pending_notifications}</span>
          )}
        </div>
      </Card>

      {status.stage === 'waiting_login' && (
        <Alert
          type="warning"
          showIcon
          message="教务登录已失效"
          description="请先在本工具箱重新完成 WebVPN 登录，追踪会在下一轮自动恢复。"
        />
      )}
      {status.last_error && (
        <Alert type="error" showIcon message="最近一次检查失败" description={status.last_error} />
      )}

      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{
          enabled: false,
          interval_minutes: 30,
          start_hour: 9,
          end_hour: 21,
          notify_initial: true,
          smtp_port: 465,
          smtp_security: 'ssl',
        }}
      >
        <Card title="检查策略" className="tracking-config-card">
          <Row gutter={[16, 0]}>
            <Col xs={24} md={8}>
              <Form.Item
                name="interval_minutes"
                label="检查间隔"
                rules={[{ required: true, message: '请输入检查间隔' }]}
              >
                <InputNumber min={5} max={1440} addonAfter="分钟" style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={12} md={8}>
              <Form.Item name="start_hour" label="每日开始">
                <Select options={hourOptions} />
              </Form.Item>
            </Col>
            <Col xs={12} md={8}>
              <Form.Item
                name="end_hour"
                label="每日结束"
                dependencies={['start_hour']}
                rules={[
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      return value > getFieldValue('start_hour')
                        ? Promise.resolve()
                        : Promise.reject(new Error('结束时间应晚于开始时间'));
                    },
                  }),
                ]}
              >
                <Select options={endHourOptions} />
              </Form.Item>
            </Col>
          </Row>
          <div className="tracking-inline-setting">
            <div>
              <strong>首次同步后发送通知</strong>
              <span>用于确认邮件和追踪流程均已正常工作。</span>
            </div>
            <Form.Item name="notify_initial" valuePropName="checked" noStyle>
              <Switch />
            </Form.Item>
          </div>
        </Card>

        <Card
          title="邮件通知"
          className="tracking-config-card"
          extra={passwordConfigured ? <Tag color="success">密码已保存</Tag> : null}
        >
          <Row gutter={[16, 0]}>
            <Col xs={24} md={16}>
              <Form.Item name="smtp_host" label="SMTP 服务器">
                <Input placeholder="例如 smtp.qq.com" autoComplete="off" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="smtp_port" label="端口">
                <InputNumber min={1} max={65535} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="smtp_security" label="连接安全">
                <Select
                  options={[
                    { value: 'ssl', label: 'SSL/TLS' },
                    { value: 'starttls', label: 'STARTTLS' },
                    { value: 'none', label: '无加密（不推荐）' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="smtp_username" label="SMTP 用户名">
                <Input autoComplete="username" placeholder="通常为完整邮箱地址" />
              </Form.Item>
            </Col>
            <Col xs={24}>
              <Form.Item
                name="smtp_password"
                label={passwordConfigured ? 'SMTP 密码（留空则保持不变）' : 'SMTP 密码或授权码'}
              >
                <Input.Password autoComplete="new-password" placeholder="推荐使用邮箱授权码" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="from_email" label="发件地址">
                <Input type="email" placeholder="sender@example.com" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="to_email" label="接收地址">
                <Input type="email" placeholder="me@example.com" />
              </Form.Item>
            </Col>
            <Col xs={24}>
              <Form.Item
                name="site_url"
                label="重新登录地址（可选）"
                extra="填写时发送随机的一次性登录页面，打开页面后才生成二维码；留空时直接发送五分钟有效的微信扫码认证链接。"
              >
                <Input placeholder="https://jwxt.example.com" />
              </Form.Item>
            </Col>
          </Row>
          <div className="tracking-card-actions">
            <Button icon={<MailOutlined />} loading={testing} onClick={testEmail}>
              发送测试邮件
            </Button>
          </div>
        </Card>
      </Form>

      <Alert
        className="tracking-note"
        type="info"
        showIcon
        message="追踪依赖本程序持续运行"
        description="Windows 请保持本地服务运行；Linux 服务会由 systemd 常驻。教务会话失效后，需要回到工具箱重新登录。"
      />
    </div>
  );
};

export default GradeTrackingPage;
