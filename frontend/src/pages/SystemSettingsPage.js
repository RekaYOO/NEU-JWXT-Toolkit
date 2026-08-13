import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button, Card, Col, Form, Input, InputNumber, Row, Select, Space, Spin, Switch, Tabs, Typography, message } from 'antd';
import { FileTextOutlined, MailOutlined, SaveOutlined, SettingOutlined } from '@ant-design/icons';
import LogsPage from './LogsPage';
import { getGradeTrackingConfig, updateGradeTrackingConfig, testGradeTrackingEmail, getSystemCacheSettings, updateSystemCacheSettings } from '../services/api';
import './SystemSettingsPage.css';

const { Title, Text } = Typography;

const CACHE_RESOURCE_META = {
  scores: { name: '成绩数据', summary: '成绩列表、绩点和课程成绩变化所使用的本地数据。' },
  'score-details': { name: '成绩详情', summary: '单门课程的分项成绩详情，通常仅在需要时更新。' },
  'academic-report': { name: '培养计划', summary: '培养方案、课程要求和完成情况，用于培养计划与 GPA 模拟。' },
  'research-training': { name: '科研训练', summary: '科研训练项目和报名记录。' },
  'festival-activities': { name: '四节活动', summary: '四节活动、报名状态和可导出活动数据。' },
  'personal-timetable': { name: '我的课表', summary: '当前及紧邻下一学期的个人课表，优先显示本地数据并在后台更新。' },
  avatar: { name: '用户头像', summary: '登录后显示的个人头像资源。' },
  'course-outline-metadata': { name: '大纲元数据', summary: '课程大纲中的考核方式和成绩分制，不保存完整大纲正文。' },
};

const CacheSettings = () => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resources, setResources] = useState([]);
  useEffect(() => { getSystemCacheSettings().then(data => { const rows = Object.entries(data.cache || {}).map(([resource, value]) => ({ resource, ...value })); setResources(rows); form.setFieldsValue({ resources: rows }); }).catch(() => message.error('缓存配置加载失败')).finally(() => setLoading(false)); }, [form]);
  const save = async () => { try { const values = await form.validateFields(); setSaving(true); const result = await updateSystemCacheSettings({ resources: Object.fromEntries((values.resources || []).map(item => [item.resource, { enabled: item.enabled, interval_minutes: item.interval_minutes }])) }); const rows = Object.entries(result.cache || {}).map(([resource, value]) => ({ resource, ...value })); setResources(rows); form.setFieldsValue({ resources: rows }); message.success('缓存配置已保存'); } catch (error) { if (!error.errorFields) message.error(error.response?.data?.detail || '缓存配置保存失败'); } finally { setSaving(false); } };
  if (loading) return <Spin />;
  return <Form form={form} layout="vertical"><Card title="缓存策略" extra={<Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>保存缓存配置</Button>}><Text type="secondary">关闭某项缓存后，该功能仍可在线读取，但不会继续使用本地缓存；间隔表示后台检查新数据的最短时间。</Text><div className="system-cache-list">{resources.map((item, index) => { const meta = CACHE_RESOURCE_META[item.resource] || { name: '其他数据', summary: '该项缓存由系统功能自动使用。' }; return <Card size="small" key={item.resource}><Row gutter={16} align="middle"><Col xs={24} md={8}><div><strong>{meta.name}</strong><div className="system-cache-resource-key">{item.resource}</div><Text type="secondary">{meta.summary}</Text></div></Col><Col xs={12} md={8}><Form.Item name={['resources', index, 'enabled']} valuePropName="checked" noStyle><Switch checkedChildren="启用" unCheckedChildren="关闭" /></Form.Item></Col><Col xs={12} md={8}><Form.Item name={['resources', index, 'interval_minutes']} label="检查间隔（分钟）"><InputNumber min={1} max={52560000} style={{ width: '100%' }} /></Form.Item></Col></Row><Form.Item name={['resources', index, 'resource']} hidden><Input /></Form.Item></Card>; })}</div></Card></Form>;
};

const TrackingMailForm = () => {
  const [form] = Form.useForm(); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [testing, setTesting] = useState(false); const [passwordConfigured, setPasswordConfigured] = useState(false);
  useEffect(() => { getGradeTrackingConfig().then(config => { const { smtp_password_configured, ...fields } = config; setPasswordConfigured(Boolean(smtp_password_configured)); form.setFieldsValue(fields); }).catch(() => message.error('邮件配置加载失败')).finally(() => setLoading(false)); }, [form]);
  const save = async () => { const values = await form.validateFields(); setSaving(true); try { const result = await updateGradeTrackingConfig(values); setPasswordConfigured(Boolean(result.config.smtp_password_configured)); message.success('邮件配置已保存'); } catch (error) { message.error(error.response?.data?.detail || '邮件配置保存失败'); } finally { setSaving(false); } };
  if (loading) return <Spin />;
  const test = async () => { setTesting(true); try { await testGradeTrackingEmail(); message.success('测试邮件已发送'); } catch (error) { message.error(error.response?.data?.detail || '测试邮件发送失败'); } finally { setTesting(false); } };
  return <Form form={form} layout="vertical" onFinish={save} initialValues={{ smtp_port: 465, smtp_security: 'ssl' }}><Row gutter={16}><Col xs={24} md={16}><Form.Item name="smtp_host" label="SMTP 服务器"><Input placeholder="例如 smtp.qq.com" autoComplete="off" /></Form.Item></Col><Col xs={24} md={8}><Form.Item name="smtp_port" label="端口"><InputNumber min={1} max={65535} style={{ width: '100%' }} /></Form.Item></Col><Col xs={24} md={12}><Form.Item name="smtp_security" label="连接安全"><Select options={[{ value: 'ssl', label: 'SSL/TLS' }, { value: 'starttls', label: 'STARTTLS' }, { value: 'none', label: '无加密（不推荐）' }]} /></Form.Item></Col><Col xs={24} md={12}><Form.Item name="smtp_username" label="SMTP 用户名"><Input autoComplete="username" placeholder="通常为完整邮箱地址" /></Form.Item></Col><Col xs={24}><Form.Item name="smtp_password" label={passwordConfigured ? 'SMTP 密码（留空则保持不变）' : 'SMTP 密码或授权码'}><Input.Password autoComplete="new-password" placeholder="推荐使用邮箱授权码" /></Form.Item></Col><Col xs={24} md={12}><Form.Item name="from_email" label="发件地址"><Input type="email" placeholder="sender@example.com" /></Form.Item></Col><Col xs={24} md={12}><Form.Item name="to_email" label="接收地址"><Input type="email" placeholder="me@example.com" /></Form.Item></Col></Row><Space><Button htmlType="submit" icon={<SaveOutlined />} loading={saving}>保存邮件配置</Button><Button icon={<MailOutlined />} loading={testing} onClick={test}>发送测试邮件</Button></Space></Form>;
};

export default function SystemSettingsPage() {
  const [params, setParams] = useSearchParams();
  const activeKey = params.get('tab') === 'logs' ? 'logs' : 'config';
  return <main className="system-settings-page"><div className="system-settings-heading"><SettingOutlined /><div><Title level={2}>系统设置</Title><Text type="secondary">统一管理日志、缓存和系统通知配置</Text></div></div><Tabs activeKey={activeKey} onChange={key => setParams(key === 'config' ? {} : { tab: key })} items={[{ key: 'config', label: <span><SettingOutlined /> 配置项</span>, children: <><CacheSettings /><Card title="系统邮件" className="system-settings-card"><TrackingMailForm /></Card></> }, { key: 'logs', label: <span><FileTextOutlined /> 系统日志</span>, children: <LogsPage embedded /> }]} /></main>;
}
