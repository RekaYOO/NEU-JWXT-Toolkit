import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Col, Empty, Modal, Radio, Row, Space, Spin, Tag, Typography, message } from 'antd';
import { ClockCircleOutlined, DeleteOutlined, HistoryOutlined, ReloadOutlined, SafetyOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  confirmJwxkBatch, deleteJwxkCatalogArchive, getJwxkCatalogArchives,
  getJwxkStatus, updateJwxkSettings,
} from '../services/api';
import { selectionParticipantCount } from '../utils/jwxkSchedule';
import './CourseSelectionPage.css';

const { Paragraph, Text, Title } = Typography;

const TYPE_LABELS = {
  '04': ['权重选课', '截止后按权重排序，由官方规则处理同权重情况。'],
  '02': ['抢选选课', '开放时间内按官方实时容量提交。'],
};

const CourseSelectionPage = () => {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState('');
  const [archives, setArchives] = useState([]);
  const [archiveDetail, setArchiveDetail] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const nextStatus = await getJwxkStatus();
      setStatus(nextStatus);
      if (nextStatus.primary_authenticated) {
        try { setArchives((await getJwxkCatalogArchives()).archives || []); }
        catch (_error) { setArchives([]); }
      } else {
        setArchives([]);
      }
    }
    catch (error) { message.error(error.message || '读取选课批次失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const groups = useMemo(() => {
    const result = { active: [], not_started: [], ended: [], unknown: [] };
    (status?.batches || []).forEach(batch => (result[batch.state] || result.unknown).push(batch));
    return result;
  }, [status]);

  const enter = async batch => {
    if (batch.need_confirm && !batch.confirmed) {
      setConfirming(batch.code);
      try {
        const result = await confirmJwxkBatch(batch.code);
        if (!result.success) throw new Error(result.message || '确认失败');
        await load();
      } catch (error) {
        message.error(error.message || '确认轮次须知失败');
        return;
      } finally { setConfirming(''); }
    }
    navigate(`/course-selection/${encodeURIComponent(batch.code)}/catalog`);
  };

  const removeArchive = archive => Modal.confirm({
    title: `删除“${archive.batch_name}”的课程备份？`,
    content: '删除后无法从本地恢复，不会影响官方选课记录。',
    okText: '删除备份',
    okButtonProps: { danger: true },
    cancelText: '取消',
    onOk: async () => {
      await deleteJwxkCatalogArchive(archive.archive_id);
      setArchives(previous => previous.filter(item => item.archive_id !== archive.archive_id));
      if (archiveDetail?.archive_id === archive.archive_id) setArchiveDetail(null);
      message.success('课程备份已删除');
    },
  });

  const archiveUnderfilled = archive => (archive.courses || []).filter(course => (
    (course.eligibility_status === 'selectable' || (
      course.teaching_class_type && course.teaching_class_type !== 'ALLKC'
      && course.eligibility_status !== 'unavailable'
    ))
    && Number(course.capacity || 0) > 0
    && selectionParticipantCount(course, archive.selection_type_code) != null
    && selectionParticipantCount(course, archive.selection_type_code) < Number(course.capacity || 0)
  ));

  if (loading && !status) return <div className="course-selection-loading"><Spin size="large" tip="读取官方选课批次…" /></div>;

  return (
    <main className="course-selection-page">
      <section className="course-selection-heading">
        <div><Title level={2}>选课系统</Title><Paragraph>选择一个轮次后进入独立工作台，统一完成课程检索、方案比较、课表冲突检查与提交。</Paragraph></div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新批次</Button>
      </section>
      <Alert showIcon type="info" icon={<SafetyOutlined />} message="课程和批次状态实时读取" description="登录状态失效时，页面会引导你重新登录选课系统。" />
      <Card className="course-selection-settings" title="选课系统线路">
        <div className="course-selection-settings__body">
          <Radio.Group value={status?.network_mode || 'follow'} disabled={saving} onChange={async event => {
            setSaving(true);
            try { setStatus(await updateJwxkSettings(event.target.value)); }
            catch (error) { message.error(error.message || '保存线路设置失败'); }
            finally { setSaving(false); }
          }}>
            <Radio.Button value="follow">跟随教务</Radio.Button><Radio.Button value="direct">直连</Radio.Button><Radio.Button value="webvpn">WebVPN</Radio.Button>
          </Radio.Group>
          <Text type="secondary">当前有效线路：{status?.effective_network_mode === 'webvpn' ? 'WebVPN' : '直连'}</Text>
        </div>
      </Card>
      {!status?.service_authenticated && <Alert type="warning" showIcon message={status?.message || '请先完成统一认证登录'} />}
      {[['active', '正在进行'], ['not_started', '即将开始'], ['ended', '已结束'], ['unknown', '状态待确认']].map(([key, title]) => groups[key]?.length > 0 && (
        <section className="course-selection-section" key={key}>
          <div className="course-selection-section__title"><div><Title level={4}>{title}</Title><Text type="secondary">{groups[key].length} 个轮次</Text></div></div>
          <Row gutter={[16, 16]}>{groups[key].map(batch => {
            const [type, description] = TYPE_LABELS[batch.selection_type_code] || [batch.selection_type || '选课轮次', batch.tactic_name || '以官方规则为准'];
            return <Col xs={24} md={12} xl={8} key={batch.code}><Card className={`course-selection-batch is-${batch.state}`}>
              <Space wrap><Tag color={batch.state === 'active' ? 'success' : 'blue'}>{type}</Tag>{batch.term_name && <Tag>{batch.term_name}</Tag>}{batch.account_selectable && <Tag color="processing">账号可进入</Tag>}</Space>
              <Title level={4}>{batch.name}</Title><Paragraph type="secondary">{description}</Paragraph>
              <div className="course-selection-time"><ClockCircleOutlined /><span>{dayjs(batch.begin_time).format('YYYY-MM-DD HH:mm')} — {dayjs(batch.end_time).format('YYYY-MM-DD HH:mm')}</span></div>
              {batch.need_confirm && !batch.confirmed && <Paragraph className="course-selection-confirm">进入前需阅读并确认官方轮次须知</Paragraph>}
              <Button block type={batch.state === 'active' ? 'primary' : 'default'} loading={confirming === batch.code} disabled={!status?.service_authenticated || !batch.account_selectable} onClick={() => enter(batch)}>{batch.state === 'ended' ? '查看结果' : '进入选课工作台'}</Button>
            </Card></Col>;
          })}</Row>
        </section>
      ))}
      {archives.length > 0 && (
        <section className="course-selection-section course-selection-archives">
          <div className="course-selection-section__title"><div><Title level={4}>课程备份</Title><Text type="secondary">进入轮次后后台静默整理本轮完整课程目录；轮次结束后保留，直到你手动删除</Text></div></div>
          <Row gutter={[16, 16]}>{archives.map(archive => {
            const underfilled = archiveUnderfilled(archive);
            const selectable = (archive.courses || []).filter(course => course.eligibility_status === 'selectable').length;
            return <Col xs={24} md={12} xl={8} key={archive.archive_id}><Card className="course-selection-archive-card">
              <Space wrap>
                <Tag color={archive.archived ? 'default' : 'processing'}>{archive.archived ? '历史备份' : '持续记录中'}</Tag>
                {!archive.archived && <Tag color={archive.sync_status === 'complete' ? 'success' : archive.sync_status === 'failed' ? 'error' : 'processing'}>{archive.sync_status === 'complete' ? '完整目录已保存' : archive.sync_status === 'failed' ? '目录同步失败' : archive.sync_status === 'running' ? `正在整理 ${archive.sync_loaded || 0}/${archive.sync_total || '?'}` : '等待整理完整目录'}</Tag>}
                {archive.archived && <Tag color={archive.final_refresh_status === 'complete' ? 'success' : 'warning'}>{archive.final_refresh_status === 'complete' ? '最终人数已更新' : '未完成最终刷新'}</Tag>}
                {archive.term_name && <Tag>{archive.term_name}</Tag>}
              </Space>
              <Title level={4}>{archive.batch_name}</Title>
              <div className="course-selection-archive-stats"><span><b>{archive.courses?.length || 0}</b> 个教学班</span><span><b>{selectable}</b> 个确认可选</span><span><b>{underfilled.length}</b> 个未报满</span></div>
              <Text type="secondary">{archive.final_refresh_at ? `最终人数更新：${dayjs(archive.final_refresh_at).format('YYYY-MM-DD HH:mm')}` : `最近记录：${dayjs(archive.updated_at).format('YYYY-MM-DD HH:mm')}`}</Text>
              <Space wrap className="course-selection-archive-actions">
                <Button type="primary" onClick={() => navigate(`/course-selection/archive/${encodeURIComponent(archive.archive_id)}`)}>进入只读工作台</Button>
                <Button icon={<HistoryOutlined />} onClick={() => setArchiveDetail(archive)}>查看未报满课程</Button>
                <Button danger icon={<DeleteOutlined />} onClick={() => removeArchive(archive)}>删除</Button>
              </Space>
            </Card></Col>;
          })}</Row>
        </section>
      )}
      <Modal open={Boolean(archiveDetail)} title={archiveDetail?.batch_name || '课程备份'} footer={null} width={760} onCancel={() => setArchiveDetail(null)}>
        <div className="course-selection-archive-list">
          {archiveDetail && archiveUnderfilled(archiveDetail).length > 0 ? archiveUnderfilled(archiveDetail)
            .sort((left, right) => (Number(left.capacity || 0) - selectionParticipantCount(left, archiveDetail.selection_type_code)) - (Number(right.capacity || 0) - selectionParticipantCount(right, archiveDetail.selection_type_code)))
            .map(course => <div className="course-selection-archive-row" key={course.class_id}>
              <div><strong>{course.course_name}</strong><span>{course.course_code || '课程代码待定'} · {course.teacher || '教师待定'}</span></div>
              <Tag color="success">{archiveDetail.selection_type_code === '04' ? '容量内余' : '余'} {Number(course.capacity || 0) - selectionParticipantCount(course, archiveDetail.selection_type_code)} / {course.capacity}</Tag>
            </div>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前备份中没有确认可选且未报满的课程" />}
        </div>
      </Modal>
    </main>
  );
};

export default CourseSelectionPage;
