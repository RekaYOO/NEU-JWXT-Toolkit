import React, {
  useCallback, useEffect, useMemo, useRef, useState
} from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Empty, Form, Grid, Input,
  Modal, Pagination, Row, Space, Spin, Statistic, Tabs, Tag, Tooltip,
  Typography, message
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, EyeOutlined, HeartFilled,
  HeartOutlined, MailOutlined, PhoneOutlined, ReadOutlined, ReloadOutlined,
  SearchOutlined, TeamOutlined, UserOutlined
} from '@ant-design/icons';
import {
  cancelResearchEnrollment,
  enrollResearchTopic,
  getResearchTopic,
  setResearchTopicFavorite,
} from '../services/api';
import { useCachedResource } from '../resources/ResourceStore';
import './ResearchTrainingPage.css';

const { Text, Title, Paragraph } = Typography;
const EMPTY_FILTERS = { keyword: '', project_name: '', advisor_name: '' };
const PAGE_SIZE = 20;

const updateSummary = (result) => {
  const changes = result.changes || {};
  if (changes.new_batch) return '科研训练报名批次已经更新。';
  const parts = [];
  if (changes.added) parts.push(`新增 ${changes.added} 个课题`);
  if (changes.updated) parts.push(`${changes.updated} 个课题信息有变化`);
  if (changes.removed) parts.push(`${changes.removed} 个课题已下架`);
  if (changes.confirmed_changed) parts.push('已确认课题状态有变化');
  return parts.length ? `${parts.join('，')}。` : '课题数据已有更新。';
};

const ResearchTrainingPage = () => {
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [form] = Form.useForm();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [favoriteLoading, setFavoriteLoading] = useState('');
  const [activeTab, setActiveTab] = useState('topics');
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState(EMPTY_FILTERS);
  const [detail, setDetail] = useState(null);
  const [enrollTopic, setEnrollTopic] = useState(null);
  const [error, setError] = useState('');
  const [syncWarning, setSyncWarning] = useState('');
  const dismissedRevision = useRef('');
  const updateModalRef = useRef(null);
  const resource = useCachedResource('research-training');

  const applySnapshot = useCallback((snapshot) => {
    setData(snapshot);
    setPage(1);
    setError('');
    setSyncWarning('');
  }, []);

  const promptForUpdate = useCallback((
    result, displayedRevision, availableRevision,
  ) => {
    const revision = availableRevision || result.revision;
    if (
      !revision
      || revision === displayedRevision
      || dismissedRevision.current === revision
    ) return;
    updateModalRef.current?.destroy();
    updateModalRef.current = Modal.confirm({
      title: '发现科研训练课题更新',
      content: `${updateSummary(result)}是否刷新当前页面？`,
      okText: '立即刷新',
      cancelText: '稍后',
      onOk: () => {
        resource.applyAvailable();
        applySnapshot(resource.availableData || result);
        updateModalRef.current = null;
      },
      onCancel: () => {
        dismissedRevision.current = revision;
        updateModalRef.current = null;
      },
    });
  }, [applySnapshot, resource.applyAvailable, resource.availableData]);

  useEffect(() => () => {
    updateModalRef.current?.destroy();
    updateModalRef.current = null;
  }, []);

  useEffect(() => {
    if (resource.data?.available) {
      applySnapshot(resource.data);
      setLoading(false);
    } else if (!resource.loading && resource.data && !resource.data.available) {
      setLoading(true);
    }
  }, [applySnapshot, resource.data, resource.loading]);

  useEffect(() => {
    setRefreshing(['starting', 'queued', 'running'].includes(resource.syncState));
    if (resource.syncError) {
      if (data?.available) {
        setSyncWarning('已显示本地缓存，但暂时无法检查教务系统中的最新课题。');
      } else {
        setError(resource.syncError);
        setLoading(false);
      }
    }
  }, [data?.available, resource.syncError, resource.syncState]);

  useEffect(() => {
    if (!resource.updateAvailable || !resource.availableData) return;
    promptForUpdate(
      resource.availableData,
      resource.displayedRevision,
      resource.availableRevision,
    );
  }, [
    promptForUpdate,
    resource.availableData,
    resource.availableRevision,
    resource.displayedRevision,
    resource.updateAvailable,
  ]);

  useEffect(() => {
    if (
      !data?.available
      && resource.availableData?.available
      && resource.syncState === 'completed'
    ) {
      resource.applyAvailable();
      applySnapshot(resource.availableData);
      setLoading(false);
    }
  }, [
    applySnapshot,
    data?.available,
    resource.applyAvailable,
    resource.availableData,
    resource.syncState,
  ]);

  const refreshNow = async ({ silentSuccess = false } = {}) => {
    setRefreshing(true);
    setSyncWarning('');
    try {
      await resource.refresh();
      const result = await resource.reloadAndApply();
      if (!result) return null;
      applySnapshot(result);
      dismissedRevision.current = '';
      if (!silentSuccess) message.success('科研训练课题已刷新');
      return result;
    } catch (requestError) {
      message.error(requestError.response?.data?.detail || '刷新科研训练课题失败');
      return null;
    } finally {
      setRefreshing(false);
    }
  };

  const applyFilters = () => {
    setFilters(draftFilters);
    setPage(1);
  };

  const resetFilters = () => {
    setDraftFilters(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setPage(1);
  };

  const allTopics = data?.topics || [];
  const filteredTopics = useMemo(() => {
    const keyword = filters.keyword.trim().toLocaleLowerCase();
    const projectName = filters.project_name.trim().toLocaleLowerCase();
    const advisorName = filters.advisor_name.trim().toLocaleLowerCase();
    return allTopics.filter((topic) => (
      (!keyword || String(topic.title || '').toLocaleLowerCase().includes(keyword))
      && (!projectName || String(topic.project_name || '').toLocaleLowerCase().includes(projectName))
      && (!advisorName || String(topic.advisor_name || '').toLocaleLowerCase().includes(advisorName))
    ));
  }, [allTopics, filters]);
  const visibleTopics = filteredTopics.slice(
    (page - 1) * PAGE_SIZE,
    page * PAGE_SIZE,
  );
  const favoriteIds = useMemo(
    () => new Set(data?.favorite_topic_ids || []),
    [data?.favorite_topic_ids],
  );

  const showDetail = async (topic) => {
    if (topic.expired) return;
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

  const toggleFavorite = async (topic, currentlyFavorite) => {
    const batchId = topic.favorite_batch_id || data?.batch?.batch_id;
    const requestKey = `${batchId}:${topic.topic_id}`;
    setFavoriteLoading(requestKey);
    try {
      const result = await setResearchTopicFavorite({
        batch_id: batchId,
        topic_id: topic.topic_id,
        favorite: !currentlyFavorite,
      });
      const nextData = {
        ...data,
        favorite_topic_ids: result.favorite_topic_ids,
        favorite_topics: result.favorite_topics,
      };
      setData(nextData);
      resource.updateData((current) => ({
        ...current,
        favorite_topic_ids: result.favorite_topic_ids,
        favorite_topics: result.favorite_topics,
      }));
      message.success(currentlyFavorite ? '已取消收藏' : '已收藏课题');
    } catch (requestError) {
      message.error(requestError.response?.data?.detail || '更新收藏失败');
    } finally {
      setFavoriteLoading('');
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
      await refreshNow({ silentSuccess: true });
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
          await refreshNow({ silentSuccess: true });
        } catch (requestError) {
          message.error(requestError.response?.data?.detail || '取消报名失败');
        }
      },
    });
  };

  const renderTopic = (topic, { favoriteView = false } = {}) => {
    const batchId = topic.favorite_batch_id || data?.batch?.batch_id || '';
    const requestKey = `${batchId}:${topic.topic_id}`;
    const isFavorite = favoriteView || favoriteIds.has(topic.topic_id);
    return (
      <Card className={`research-topic-card${topic.expired ? ' research-topic-card--expired' : ''}`}
        key={requestKey}>
        <div className="research-topic-card__header">
          <div>
            <Title level={5}>{topic.title || '未命名课题'}</Title>
            <Text type="secondary">{topic.project_name || '未填写隶属科研项目'}</Text>
          </div>
          <Space size={6} wrap>
            {topic.expired ? (
              <Tag>已过期</Tag>
            ) : topic.is_registered ? (
              <Tag color="processing">{topic.registration_status || '已报名'}</Tag>
            ) : topic.is_full ? (
              <Tag color="default">名额已满</Tag>
            ) : (
              <Tag color="success">可报名</Tag>
            )}
            <Tooltip title={isFavorite ? '取消收藏' : '收藏课题'}>
              <Button
                className="research-favorite-button"
                type="text"
                aria-label={isFavorite ? '取消收藏课题' : '收藏课题'}
                icon={isFavorite ? <HeartFilled /> : <HeartOutlined />}
                loading={favoriteLoading === requestKey}
                onClick={() => toggleFavorite(topic, isFavorite)}
              />
            </Tooltip>
          </Space>
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
        {topic.expired && topic.favorite_batch_name && (
          <Text className="research-topic-card__archive" type="secondary">
            收藏自：{topic.favorite_batch_name}
          </Text>
        )}
        <div className="research-topic-card__actions">
          <Button icon={<EyeOutlined />} disabled={topic.expired}
            onClick={() => showDetail(topic)}>
            {topic.expired ? '课题已下架' : '查看详情'}
          </Button>
          {!topic.expired && (topic.is_registered ? (
            <Button danger disabled={!topic.can_cancel}
              onClick={() => cancelEnrollment(topic)}>
              取消报名
            </Button>
          ) : (
            <Button type="primary"
              disabled={!topic.can_enroll || !data?.eligibility?.available}
              onClick={() => openEnroll(topic)}>
              报名
            </Button>
          ))}
        </div>
      </Card>
    );
  };

  const batch = data?.batch;
  const eligibility = data?.eligibility;
  const savedAt = data?.saved_at ? new Date(data.saved_at).toLocaleString() : '';

  return (
    <div className="research-training-page">
      <div className="research-page-header">
        <div>
          <Title level={3}><ReadOutlined /> 科研训练</Title>
          <Text type="secondary">{batch?.name || '学生科研训练课题报名与状态查询'}</Text>
          {savedAt && <Text className="research-cache-time" type="secondary">本地数据更新于 {savedAt}</Text>}
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => refreshNow()}
          loading={refreshing}>
          刷新
        </Button>
      </div>

      {error && <Alert type="error" showIcon message={error} />}
      {syncWarning && <Alert className="research-sync-alert" type="warning"
        showIcon message={syncWarning} />}
      {eligibility && !eligibility.available && (
        <Alert className="research-eligibility-alert" type="warning" showIcon
          message="报名资格数据缺失" description={eligibility.reason} />
      )}

      {batch && (
        <Row gutter={[12, 12]} className="research-rule-grid">
          <Col xs={12} md={6}><Card><Statistic title="最多报名" value={batch.max_topics} suffix="项" /></Card></Col>
          <Col xs={12} md={6}><Card><Statistic title="专业排名要求" value={batch.rank_limit_percent} suffix="%" prefix="前" /></Card></Col>
          <Col xs={12} md={6}>
            <Card>
              <Statistic title="不及格成绩"
                value={batch.allow_failed_courses ? '允许' : '不允许'}
                prefix={batch.allow_failed_courses
                  ? <CheckCircleOutlined /> : <CloseCircleOutlined />} />
            </Card>
          </Col>
          <Col xs={12} md={6}><Card><Statistic title="课题总数" value={data?.total || 0} /></Card></Col>
        </Row>
      )}

      <Spin spinning={loading}>
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          {
            key: 'topics',
            label: '课题报名',
            children: (
              <>
                <Card className="research-filter-card">
                  <div className="research-filter-grid">
                    <Input allowClear placeholder="研究题目或方向"
                      value={draftFilters.keyword}
                      onChange={(event) => setDraftFilters({
                        ...draftFilters, keyword: event.target.value
                      })}
                      onPressEnter={applyFilters} />
                    <Input allowClear placeholder="科研项目名称"
                      value={draftFilters.project_name}
                      onChange={(event) => setDraftFilters({
                        ...draftFilters, project_name: event.target.value
                      })}
                      onPressEnter={applyFilters} />
                    <Input allowClear placeholder="导师姓名"
                      value={draftFilters.advisor_name}
                      onChange={(event) => setDraftFilters({
                        ...draftFilters, advisor_name: event.target.value
                      })}
                      onPressEnter={applyFilters} />
                    <Space>
                      <Button type="primary" icon={<SearchOutlined />}
                        onClick={applyFilters}>搜索</Button>
                      <Button onClick={resetFilters}>清空</Button>
                    </Space>
                  </div>
                </Card>
                {visibleTopics.length ? (
                  <div className="research-topic-grid">
                    {visibleTopics.map((topic) => renderTopic(topic))}
                  </div>
                ) : (!loading && <Empty description="当前条件下没有课题" />)}
                {filteredTopics.length > PAGE_SIZE && (
                  <Pagination className="research-pagination" current={page}
                    pageSize={PAGE_SIZE} total={filteredTopics.length}
                    showSizeChanger={false} onChange={setPage} />
                )}
              </>
            ),
          },
          {
            key: 'favorites',
            label: `我收藏的课题${data?.favorite_topics?.length ? ` (${data.favorite_topics.length})` : ''}`,
            children: data?.favorite_topics?.length ? (
              <div className="research-topic-grid">
                {data.favorite_topics.map((topic) => renderTopic(
                  topic,
                  { favoriteView: true },
                ))}
              </div>
            ) : <Empty description="暂未收藏课题" />,
          },
          {
            key: 'confirmed',
            label: '已确认课题',
            children: data?.confirmed_topics?.length ? (
              <div className="research-topic-grid">
                {data.confirmed_topics.map((topic) => (
                  <Card className="research-topic-card" key={topic.record_id}>
                    <Title level={5}>{topic.title}</Title>
                    <Paragraph type="secondary">
                      {topic.project_name || '未填写科研项目名称'}
                    </Paragraph>
                    <div className="research-topic-card__meta">
                      <span><UserOutlined /> {topic.advisor_name || '导师待定'}</span>
                      <span><ReadOutlined /> 科研记录 {topic.journal_count} 次</span>
                    </div>
                    {topic.score && <Tag color="success">成绩：{topic.score}</Tag>}
                  </Card>
                ))}
              </div>
            ) : (!loading && <Empty description="暂无已确认课题" />),
          },
        ]} />
      </Spin>

      <Drawer title="课题详情" width={isMobile ? '100%' : 640}
        open={Boolean(detail)} onClose={() => setDetail(null)}>
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
        okText="确认报名" cancelText="取消" confirmLoading={submitting}
        destroyOnClose>
        {enrollTopic && (
          <>
            <Alert type="info" showIcon message={enrollTopic.title}
              description="提交后将产生真实报名记录，请确认联系方式无误。" />
            <Descriptions size="small" column={2}
              className="research-eligibility-summary">
              <Descriptions.Item label="平均绩点">{eligibility?.gpa || '—'}</Descriptions.Item>
              <Descriptions.Item label="专业排名">{eligibility?.major_rank || '—'}</Descriptions.Item>
            </Descriptions>
            <Form form={form} layout="vertical">
              <Form.Item name="phone" label="联系电话"
                rules={[{ required: true, message: '请输入联系电话' }, { max: 16 }]}>
                <Input prefix={<PhoneOutlined />} autoComplete="tel" />
              </Form.Item>
              <Form.Item name="email" label="电子邮箱" rules={[
                { required: true, message: '请输入电子邮箱' },
                { type: 'email', message: '电子邮箱格式不正确' },
                { max: 20 },
              ]}>
                <Input prefix={<MailOutlined />} autoComplete="email" />
              </Form.Item>
              <Form.Item name="reason" label="申请理由"
                rules={[{ max: 300 }]}>
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
