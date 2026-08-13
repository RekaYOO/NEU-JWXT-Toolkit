import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
  notification,
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
import { MobileActionBar } from '../components/mobile/MobileUX';
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
  const [status, setStatus] = useState({ stage: 'disabled', enabled: false });
  const navigate = useNavigate();

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
        const { enabled: configuredEnabled, ...fields } = config;
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
      const values = await form.validateFields();
      const trackingValues = (({ interval_minutes, start_hour, end_hour, site_url }) => ({
        interval_minutes, start_hour, end_hour, site_url,
      }))(values);
      setSaving(true);
      const result = await updateGradeTrackingConfig(trackingValues);
      const { enabled: configuredEnabled, ...fields } = result.config;
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
      message.success(
        nextEnabled
          ? '成绩追踪已开启，初始邮件将在同步完成后自动发送'
          : '成绩追踪已关闭'
      );
    } catch (error) {
      const detail = String(error?.response?.data?.detail || '');
      if (nextEnabled && detail.includes('SMTP')) {
        notification.warning({
          message: '无法启用成绩追踪',
          description: '请先在系统设置中完善 SMTP 服务器、发件地址和收件地址。',
          duration: 0,
          btn: <Button size="small" type="primary" onClick={() => navigate('/system-settings?tab=config')}>去系统设置</Button>,
        });
        return;
      }
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
          <Space wrap className="tracking-desktop-actions">
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
          interval_minutes: 30,
          start_hour: 9,
          end_hour: 21,
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
              <strong>开启后发送初始邮件</strong>
              <span>每次开启成绩追踪，都会同步当前成绩并自动发送一封初始邮件。</span>
            </div>
          </div>
        </Card>

        <Card title="登录恢复" className="tracking-config-card">
          <Form.Item name="site_url" label="重新登录地址（可选）" extra="填写时发送随机的一次性登录页面，打开页面后才生成二维码；留空时直接发送五分钟有效的微信扫码认证链接。">
            <Input type="url" inputMode="url" autoCapitalize="none" autoCorrect="off" placeholder="https://jwxt.example.com" />
          </Form.Item>
        </Card>
      </Form>

      <Alert
        className="tracking-note"
        type="info"
        showIcon
        message="追踪依赖本程序持续运行"
        description="Windows 请保持本地服务运行；Linux 服务会由 systemd 常驻。教务会话失效后，需要回到工具箱重新登录。"
      />

      <MobileActionBar className="tracking-mobile-action-bar">
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
      </MobileActionBar>
    </div>
  );
};

export default GradeTrackingPage;
