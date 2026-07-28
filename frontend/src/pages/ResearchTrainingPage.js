import React, { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Empty, Form, Grid, Input,
  Modal, Pagination, Row, Space, Spin, Statistic, Tabs, Tag, Typography,
  message
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, EyeOutlined, MailOutlined,
  PhoneOutlined, ReadOutlined, ReloadOutlined, SearchOutlined, TeamOutlined,
  UserOutlined
} from '@ant-design/icons';
import {
  cancelResearchEnrollment,
  enrollResearchTopic,
  getConfirmedResearchTopics,
  getResearchTopic,
  getResearchTraining,
} from '../services/api';
import './ResearchTrainingPage.css';

const { Text, Title, Paragraph } = Typography;
const EMPTY_FILTERS = { keyword: '', project_name: '', advisor_name: '' };

const ResearchTrainingPage = () => {
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [form] = Form.useForm();
  const [data, setData] = useState(null);
  const [topics, setTopics] = useState([]);
  const [confirmedTopics, setConfirmedTopics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [confirmedLoading, setConfirmedLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState('topics');
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState(EMPTY_FILTERS);
  const [detail, setDetail] = useState(null);
  const [enrollTopic, setEnrollTopic] = useState(null);
  const [error, setError] = useState('');

  const loadTopics = async (targetPage = page, targetFilters = filters) => {
    setLoading(true);
    setError('');
    try {
      const result = await getResearchTraining({
        page: targetPage,
        page_size: 20,
        ...targetFilters,
      });
      setData(result);
      setTopics(result.topics || []);
      setPage(result.page || targetPage);
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '获取科研训练课题失败');
    } finally {
      setLoading(false);
    }
  };

  const loadConfirmed = async () => {
    setConfirmedLoading(true);
    try {
      const result = await getConfirmedResearchTopics();
      setConfirmedTopics(result.topics || []);
    } catch (requestError) {
      message.error(requestError.response?.data?.detail || '获取已确认课题失败');
    } finally {
      setConfirmedLoading(false);
    }
  };

  useEffect(() => {
    loadTopics(1, EMPTY_FILTERS);
  }, []);

  const applyFilters = () => {
    setFilters(draftFilters);
    setPage(1);
    loadTopics(1, draftFilters);
  };

  const resetFilters = () => {
    setDraftFilters(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setPage(1);
    loadTopics(1, EMPTY_FILTERS);
  };

  const showDetail = async (topic) => {
    setDetailLoading(true);
    setDetail({ title: topic.title });
    try {
      setDetail(await getResearchTopic(topic.topic_id));
    } catch (requestError) {
      message.error(requestError.response?.data?.detail || '获取课题详情失败');
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const openEnroll = (topic) => {
    if (!data?.eligibility?.available) {
      message.warning(data?.eligibility?.reason || '当前资格信息不完整，暂时无法报名');
      return;
    }
    setEnrollTopic(topic);
    form.resetFields();
  };

  const submitEnrollment = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const result = await enrollResearchTopic({
        topic_id: enrollTopic.topic_id,
        batch_id: data.batch.batch_id,
        phone: values.phone,
        email: values.email,
        reason: values.reason || '',
      });
      message.success(result.message || '报名成功');
      setEnrollTopic(null);
      await loadTopics(page, filters);
    } catch (requestError) {
      if (requestError?.errorFields) return;
      message.error(requestError.response?.data?.detail || '报名失败');
    } finally {
      setSubmitting(false);
    }
  };

  const cancelEnrollment = (topic) => {
    Modal.confirm({
      title: '确认取消报名？',
      content: `将取消“${topic.title}”的报名记录。`,
      okText: '取消报名',
      okButtonProps: { danger: true },
      cancelText: '返回',
      onOk: async () => {
        try {
          const result = await cancelResearchEnrollment(topic.topic_id);
          message.success(result.message || '已取消报名');
          await loadTopics(page, filters);
        } catch (requestError) {
          message.error(requestError.response?.data?.detail || '取消报名失败');
        }
      },
    });
  };

  const renderTopic = (topic) => (
    <Card className="research-topic-card" key={topic.topic_id}>
      <div className="research-topic-card__header">
        <div>
          <Title level={5}>{topic.title || '未命名课题'}</Title>
          <Text type="secondary">{topic.project_name || '未填写隶属科研项目'}</Text>
        </div>
        {topic.is_registered ? (
          <Tag color="processing">{topic.registration_status || '已报名'}</Tag>
        ) : topic.is_full ? (
          <Tag color="default">名额已满</Tag>
        ) : (
          <Tag color="success">可报名</Tag>
        )}
      </div>
      <div className="research-topic-card__meta">
        <span><UserOutlined /> {topic.advisor_name || '导师待定'}</span>
        <span><ReadOutlined /> {topic.major || '专业不限'}</span>
        <span>
          <TeamOutlined /> {topic.registered_count} 人已报名
          {topic.capacity > 0 ? ` / 限 ${topic.capacity} 人` : ' / 名额未公布'}
        </span>
      </div>
      <Text className="research-topic-card__college" type="secondary">
        {topic.college || topic.advisor_college || '院系信息未提供'}
      </Text>
      <div className="research-topic-card__actions">
        <Button icon={<EyeOutlined />} onClick={() => showDetail(topic)}>
          查看详情
        </Button>
        {topic.is_registered ? (
          <Button danger disabled={!topic.can_cancel} onClick={() => cancelEnrollment(topic)}>
            取消报名
          </Button>
        ) : (
          <Button
            type="primary"
            disabled={!topic.can_enroll || !data?.eligibility?.available}
            onClick={() => openEnroll(topic)}
          >
            报名
          </Button>
        )}
      </div>
    </Card>
  );

  const batch = data?.batch;
  const eligibility = data?.eligibility;

  return (
    <div className="research-training-page">
      <div className="research-page-header">
        <div>
          <Title level={3}><ReadOutlined /> 科研训练</Title>
          <Text type="secondary">{batch?.name || '学生科研训练课题报名与状态查询'}</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => loadTopics(page, filters)} loading={loading}>
          刷新
        </Button>
      </div>

      {error && <Alert type="error" showIcon message={error} />}
      {eligibility && !eligibility.available && (
        <Alert
          className="research-eligibility-alert"
          type="warning"
          showIcon
          message="报名资格数据缺失"
          description={eligibility.reason}
        />
      )}

      {batch && (
        <Row gutter={[12, 12]} className="research-rule-grid">
          <Col xs={12} md={6}><Card><Statistic title="最多报名" value={batch.max_topics} suffix="项" /></Card></Col>
          <Col xs={12} md={6}><Card><Statistic title="专业排名要求" value={batch.rank_limit_percent} suffix="%" prefix="前" /></Card></Col>
          <Col xs={12} md={6}>
            <Card>
              <Statistic
                title="不及格成绩"
                value={batch.allow_failed_courses ? '允许' : '不允许'}
                prefix={batch.allow_failed_courses ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
              />
            </Card>
          </Col>
          <Col xs={12} md={6}><Card><Statistic title="课题总数" value={data?.total || 0} /></Card></Col>
        </Row>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={(key) => {
          setActiveTab(key);
          if (key === 'confirmed') loadConfirmed();
        }}
        items={[
          {
            key: 'topics',
            label: '课题报名',
            children: (
              <>
                <Card className="research-filter-card">
                  <div className="research-filter-grid">
                    <Input allowClear placeholder="研究题目或方向" value={draftFilters.keyword}
                      onChange={(event) => setDraftFilters({ ...draftFilters, keyword: event.target.value })}
                      onPressEnter={applyFilters} />
                    <Input allowClear placeholder="科研项目名称" value={draftFilters.project_name}
                      onChange={(event) => setDraftFilters({ ...draftFilters, project_name: event.target.value })}
                      onPressEnter={applyFilters} />
                    <Input allowClear placeholder="导师姓名" value={draftFilters.advisor_name}
                      onChange={(event) => setDraftFilters({ ...draftFilters, advisor_name: event.target.value })}
                      onPressEnter={applyFilters} />
                    <Space>
                      <Button type="primary" icon={<SearchOutlined />} onClick={applyFilters}>搜索</Button>
                      <Button onClick={resetFilters}>清空</Button>
                    </Space>
                  </div>
                </Card>
                <Spin spinning={loading}>
                  {topics.length ? (
                    <div className="research-topic-grid">{topics.map(renderTopic)}</div>
                  ) : (!loading && <Empty description="当前条件下没有课题" />)}
                </Spin>
                {(data?.total || 0) > 20 && (
                  <Pagination className="research-pagination" current={page} pageSize={20}
                    total={data.total} showSizeChanger={false}
                    onChange={(nextPage) => {
                      setPage(nextPage);
                      loadTopics(nextPage, filters);
                    }} />
                )}
              </>
            ),
          },
          {
            key: 'confirmed',
            label: '已确认课题',
            children: (
              <Spin spinning={confirmedLoading}>
                {confirmedTopics.length ? (
                  <div className="research-topic-grid">
                    {confirmedTopics.map((topic) => (
                      <Card className="research-topic-card" key={topic.record_id}>
                        <Title level={5}>{topic.title}</Title>
                        <Paragraph type="secondary">{topic.project_name || '未填写科研项目名称'}</Paragraph>
                        <div className="research-topic-card__meta">
                          <span><UserOutlined /> {topic.advisor_name || '导师待定'}</span>
                          <span><ReadOutlined /> 科研记录 {topic.journal_count} 次</span>
                        </div>
                        {topic.score && <Tag color="success">成绩：{topic.score}</Tag>}
                      </Card>
                    ))}
                  </div>
                ) : (!confirmedLoading && <Empty description="暂无已确认课题" />)}
              </Spin>
            ),
          },
        ]}
      />

      <Drawer
        title="课题详情"
        width={isMobile ? '100%' : 640}
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
      >
        <Spin spinning={detailLoading}>
          {detail && (
            <>
              <Title level={4}>{detail.title}</Title>
              <Descriptions column={1} bordered size="small">
                <Descriptions.Item label="隶属项目">{detail.project_name || '—'}</Descriptions.Item>
                <Descriptions.Item label="所属专业">{detail.major || '—'}</Descriptions.Item>
                <Descriptions.Item label="所属院系">{detail.college || '—'}</Descriptions.Item>
                <Descriptions.Item label="导师">{detail.advisor_name || '—'} {detail.advisor_title || ''}</Descriptions.Item>
                <Descriptions.Item label="导师联系方式">{detail.contact || '—'}</Descriptions.Item>
                <Descriptions.Item label="招收人数">
                  {detail.capacity > 0 ? `${detail.capacity} 人` : '未公布'}
                </Descriptions.Item>
              </Descriptions>
              <Title level={5}>课题简介</Title>
              <Paragraph className="research-detail-text">{detail.introduction || '暂无简介'}</Paragraph>
              <Title level={5}>报名要求</Title>
              <Paragraph className="research-detail-text">{detail.requirements || '暂无额外要求'}</Paragraph>
            </>
          )}
        </Spin>
      </Drawer>

      <Modal title="科研训练课题报名" open={Boolean(enrollTopic)}
        onCancel={() => setEnrollTopic(null)} onOk={submitEnrollment}
        okText="确认报名" cancelText="取消" confirmLoading={submitting} destroyOnClose>
        {enrollTopic && (
          <>
            <Alert type="info" showIcon message={enrollTopic.title}
              description="提交后将产生真实报名记录，请确认联系方式无误。" />
            <Descriptions size="small" column={2} className="research-eligibility-summary">
              <Descriptions.Item label="平均绩点">{eligibility?.gpa || '—'}</Descriptions.Item>
              <Descriptions.Item label="专业排名">{eligibility?.major_rank || '—'}</Descriptions.Item>
            </Descriptions>
            <Form form={form} layout="vertical">
              <Form.Item name="phone" label="联系电话"
                rules={[{ required: true, message: '请输入联系电话' }, { max: 16 }]}>
                <Input prefix={<PhoneOutlined />} autoComplete="tel" />
              </Form.Item>
              <Form.Item name="email" label="电子邮箱"
                rules={[
                  { required: true, message: '请输入电子邮箱' },
                  { type: 'email', message: '电子邮箱格式不正确' },
                  { max: 20 },
                ]}>
                <Input prefix={<MailOutlined />} autoComplete="email" />
              </Form.Item>
              <Form.Item name="reason" label="申请理由" rules={[{ max: 300 }]}>
                <Input.TextArea rows={4} showCount maxLength={300} />
              </Form.Item>
            </Form>
          </>
        )}
      </Modal>
    </div>
  );
};

export default ResearchTrainingPage;
