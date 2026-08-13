import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Empty, Radio, Row, Space, Spin, Tag, Typography, message,
} from 'antd';
import {
  ClockCircleOutlined, ReloadOutlined, SafetyOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { getJwxkStatus, updateJwxkSettings } from '../services/api';
import './CourseSelectionPage.css';

const { Paragraph, Text, Title } = Typography;

const STATE_META = {
  not_started: { color: 'blue', text: '未开始' },
  active: { color: 'success', text: '进行中' },
  ended: { color: 'default', text: '已结束' },
  unknown: { color: 'warning', text: '时间待确认' },
};

const TYPE_META = {
  权重: {
    color: 'purple',
    description: '截止后按投放权重从高到低筛选；相同权重由官方规则处理。',
  },
  抢选: {
    color: 'orange',
    description: '先到先得、即选即中；开放前不会启动自动请求。',
  },
};

const CourseSelectionPage = () => {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setStatus(await getJwxkStatus());
    } catch (error) {
      message.error(error.message || '读取选课系统状态失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const upcoming = useMemo(() => (
    (status?.batches || []).filter(batch => batch.state === 'not_started')
  ), [status]);

  const changeNetworkMode = async event => {
    const previous = status;
    const networkMode = event.target.value;
    setStatus(current => current ? { ...current, network_mode: networkMode } : current);
    setSaving(true);
    try {
      setStatus(await updateJwxkSettings(networkMode));
      message.success('选课系统网络模式已保存');
    } catch (error) {
      setStatus(previous);
      message.error(error.message || '保存网络模式失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading && !status) {
    return <div className="course-selection-loading"><Spin size="large" tip="读取官方选课批次…" /></div>;
  }

  return (
    <main className="course-selection-page">
      <section className="course-selection-heading">
        <div>
          <Text className="page-kicker">JWXK COURSE SELECTION</Text>
          <Title level={2}>选课系统</Title>
          <Paragraph>统一管理权重选课与抢选轮次，并为课程冲突、策略投权和自动抢课提供独立接入边界。</Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新批次</Button>
      </section>

      <Card className="course-selection-settings" title="连接方式">
        <div className="course-selection-settings__body">
          <div>
            <Text strong>选课系统 WebVPN</Text>
            <Paragraph type="secondary">
              账号与登录态复用项目统一认证，仅允许为选课系统单独指定访问线路。当前实际使用：
              {status?.effective_network_mode === 'webvpn' ? 'WebVPN' : '直连'}。
            </Paragraph>
          </div>
          <Radio.Group value={status?.network_mode || 'follow'} onChange={changeNetworkMode} disabled={saving} buttonStyle="solid">
            <Radio.Button value="follow">跟随教务</Radio.Button>
            <Radio.Button value="direct">直连</Radio.Button>
            <Radio.Button value="webvpn">WebVPN</Radio.Button>
          </Radio.Group>
        </div>
      </Card>

      <Alert
        showIcon
        type="info"
        icon={<SafetyOutlined />}
        message="当前为预接入阶段"
        description="批次与时间窗来自选课系统官方首页。课程列表、已选结果及真实提交必须等轮次开放后按实际请求完成适配；程序不会猜测接口，也不会自动确认须知或提交选课。"
      />

      {!status?.available && <Alert type="warning" showIcon message={status?.message || '选课系统暂不可用'} />}

      <section className="course-selection-section">
        <div className="course-selection-section__title">
          <div>
            <Title level={4}>官方批次</Title>
            <Space wrap>
              <Text type="secondary">{upcoming.length ? `${upcoming.length} 个批次尚未开始` : '按官方当前状态显示'}</Text>
              {status?.primary_authenticated && (
                <Tag color={status?.service_authenticated ? 'success' : 'processing'}>
                  {status?.service_authenticated ? '选课会话已连接' : '统一认证已登录'}
                </Tag>
              )}
            </Space>
          </div>
        </div>
        {(status?.batches || []).length ? (
          <Row gutter={[16, 16]}>
            {status.batches.map(batch => {
              const state = STATE_META[batch.state] || STATE_META.unknown;
              const type = TYPE_META[batch.selection_type] || { color: 'blue', description: batch.tactic_name };
              return (
                <Col xs={24} lg={12} key={batch.code}>
                  <Card className={`course-selection-batch is-${batch.state}`}>
                    <Space wrap>
                      <Tag color={type.color}>{batch.selection_type || '选课'}</Tag>
                      <Tag color={state.color}>{state.text}</Tag>
                      <Text type="secondary">{batch.tactic_name}</Text>
                    </Space>
                    <Title level={4}>{batch.name}</Title>
                    <Text>{batch.term_name || batch.term_code}</Text>
                    <div className="course-selection-time">
                      <ClockCircleOutlined />
                      <span>{dayjs(batch.begin_time).format('YYYY-MM-DD HH:mm')} — {dayjs(batch.end_time).format('YYYY-MM-DD HH:mm')}</span>
                    </div>
                    <Paragraph type="secondary">{type.description}</Paragraph>
                    {batch.need_confirm && <Text className="course-selection-confirm">进入前需阅读并确认官方轮次须知</Text>}
                  </Card>
                </Col>
              );
            })}
          </Row>
        ) : <Empty description="当前未读取到选课批次" />}
      </section>
    </main>
  );
};

export default CourseSelectionPage;
