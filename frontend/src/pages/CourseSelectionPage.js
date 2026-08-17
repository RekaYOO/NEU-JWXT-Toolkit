import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Checkbox, Col, Divider, Empty, InputNumber, Modal, QRCode, Radio, Row, Select, Space, Spin, Tag, Typography, message } from 'antd';
import { ClockCircleOutlined, DeleteOutlined, HistoryOutlined, QrcodeOutlined, QuestionCircleOutlined, ReloadOutlined, SafetyOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  confirmJwxkBatch, deleteJwxkCatalogArchive, getJwxkCatalogArchives,
  getJwxkStatus, updateJwxkSettings,
  getJwxkAutomationSettings, updateJwxkAutomationSettings,
  syncJwxkAutomationTaskTimes,
  startWebVPNQRLogin, getWebVPNQRStatus, cancelWebVPNQRLogin,
} from '../services/api';
import { changedOfficialBatchTimes, selectionParticipantCount } from '../utils/jwxkSchedule';
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
  const [automationBatch, setAutomationBatch] = useState(null);
  const [automationSettings, setAutomationSettings] = useState(null);
  const [automationLoading, setAutomationLoading] = useState(false);
  const [automationSaving, setAutomationSaving] = useState(false);
  const [modelHelpOpen, setModelHelpOpen] = useState(false);
  const [webvpnLoginOpen, setWebvpnLoginOpen] = useState(false);
  const [webvpnQrFlow, setWebvpnQrFlow] = useState(null);
  const [webvpnQrLoading, setWebvpnQrLoading] = useState(false);
  const [webvpnQrMessage, setWebvpnQrMessage] = useState('');

  const promptTaskTimeSync = changes => Modal.confirm({
    title: '官方选课轮次时间已变更',
    width: 680,
    content: (
      <div className="course-selection-time-change-list">
        <Paragraph>是否将新的起止时间同步到这些轮次中尚未完成的自动任务？同步后，抢课开放时刻、任务停止时间及策略投权结束前 5/3 分钟窗口都会按新时间计算。</Paragraph>
        {changes.map(change => (
          <div key={change.batch_code}>
            <strong>{change.batch_name}</strong>
            <span>原时间：{dayjs(change.old_start_at).format('YYYY-MM-DD HH:mm')} — {dayjs(change.old_end_at).format('YYYY-MM-DD HH:mm')}</span>
            <span>新时间：{dayjs(change.start_at).format('YYYY-MM-DD HH:mm')} — {dayjs(change.end_at).format('YYYY-MM-DD HH:mm')}</span>
          </div>
        ))}
      </div>
    ),
    okText: '同步自动任务时间',
    cancelText: '暂不同步',
    onOk: async () => {
      try {
        const results = await Promise.all(changes.map(change => syncJwxkAutomationTaskTimes({
          batch_code: change.batch_code,
          start_at: change.start_at,
          end_at: change.end_at,
        })));
        const changedTaskCount = results.reduce((sum, result) => (
          sum + Number(result.changed_task_count || 0)
        ), 0);
        message.success(changedTaskCount
          ? `已同步 ${changedTaskCount} 个自动任务的轮次时间`
          : '轮次时间已确认，当前没有需要更新的未完成任务');
      } catch (error) {
        message.error(error.message || '同步自动任务时间失败');
        throw error;
      }
    },
  });

  const load = async ({ manual = false } = {}) => {
    setLoading(true);
    let timeChanges = [];
    try {
      const nextStatus = await getJwxkStatus();
      if (manual) {
        timeChanges = changedOfficialBatchTimes(status?.batches || [], nextStatus.batches || []);
      }
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
    if (timeChanges.length) promptTaskTimeSync(timeChanges);
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (!webvpnQrFlow?.flow_id) return undefined;
    let stopped = false;
    const poll = async () => {
      try {
        const result = await getWebVPNQRStatus(webvpnQrFlow.flow_id);
        if (stopped) return;
        if (result.success && result.status === 'authenticated') {
          setWebvpnQrFlow(null);
          setWebvpnLoginOpen(false);
          message.success('WebVPN 登录成功，正在重新读取选课轮次');
          await load();
        } else if (result.status === 'expired' || result.status === 'missing') {
          setWebvpnQrFlow(null);
          setWebvpnQrMessage('二维码已失效，请重新获取。');
        } else if (!result.success || result.status === 'error') {
          setWebvpnQrFlow(null);
          setWebvpnQrMessage(result.message || '二维码登录失败，请重新获取。');
        }
      } catch (_error) {
        if (!stopped) setWebvpnQrMessage('暂时无法检查二维码状态，系统会继续重试。');
      }
    };
    poll();
    const timer = window.setInterval(poll, Math.max(1, Number(webvpnQrFlow.poll_interval || 2)) * 1000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [webvpnQrFlow]);

  const openWebvpnQrLogin = async () => {
    setWebvpnLoginOpen(true);
    setWebvpnQrMessage('');
    setWebvpnQrLoading(true);
    try {
      const result = await startWebVPNQRLogin('');
      if (!result.success) throw new Error(result.message || '无法获取二维码');
      setWebvpnQrFlow(result);
    } catch (error) {
      setWebvpnQrMessage(error.message || '无法获取 WebVPN 登录二维码。');
    } finally {
      setWebvpnQrLoading(false);
    }
  };

  const closeWebvpnQrLogin = async () => {
    const flowId = webvpnQrFlow?.flow_id;
    setWebvpnQrFlow(null);
    setWebvpnLoginOpen(false);
    setWebvpnQrMessage('');
    if (flowId) await cancelWebVPNQRLogin(flowId).catch(() => {});
  };

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

  const openAutomation = async batch => {
    setAutomationBatch(batch);
    setAutomationLoading(true);
    try { setAutomationSettings(await getJwxkAutomationSettings(batch.code)); }
    catch (error) { message.error(error.message || '读取本轮自动化配置失败'); setAutomationBatch(null); }
    finally { setAutomationLoading(false); }
  };

  const saveAutomation = async () => {
    if (!automationBatch || !automationSettings) return;
    setAutomationSaving(true);
    try { setAutomationSettings(await updateJwxkAutomationSettings(automationBatch.code, automationSettings)); message.success('本轮自动化配置已保存'); setAutomationBatch(null); }
    catch (error) { message.error(error.message || '保存自动化配置失败'); }
    finally { setAutomationSaving(false); }
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
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load({ manual: true })}>刷新批次</Button>
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
      {!status?.service_authenticated && (
        <Alert
          type="warning"
          showIcon
          message={status?.message || '请先完成登录'}
          action={status?.service_auth_state === 'login_required' ? (
            <Button size="small" type="primary" icon={<QrcodeOutlined />} onClick={openWebvpnQrLogin}>
              扫码登录 WebVPN
            </Button>
          ) : (
            <Button size="small" onClick={() => load({ manual: true })}>重新检查</Button>
          )}
          description={status?.service_auth_state === 'login_required'
            ? '选课线路已切换为 WebVPN。完成登录后返回本页并刷新，即可读取账号轮次和课程。'
            : undefined}
        />
      )}
      <Modal
        open={webvpnLoginOpen}
        title="登录 WebVPN"
        footer={null}
        onCancel={closeWebvpnQrLogin}
        destroyOnHidden
      >
        <div style={{ minHeight: 250, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
          {webvpnQrMessage && <Alert type="warning" showIcon message={webvpnQrMessage} style={{ width: '100%' }} />}
          {webvpnQrFlow?.qr_content ? (
            <>
              <QRCode value={webvpnQrFlow.qr_content} size={210} status="active" />
              <Text type="secondary">使用已关注东北大学微信企业号的微信扫码</Text>
            </>
          ) : (
            <Button type="primary" icon={<QrcodeOutlined />} loading={webvpnQrLoading} onClick={openWebvpnQrLogin}>
              {webvpnQrLoading ? '正在获取二维码' : '重新获取二维码'}
            </Button>
          )}
        </div>
      </Modal>
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
              <Space direction="vertical" style={{ width: '100%' }}>
                <Button block type={batch.state === 'active' ? 'primary' : 'default'} loading={confirming === batch.code} disabled={!status?.service_authenticated || !batch.account_selectable} onClick={() => enter(batch)}>{batch.state === 'ended' ? '查看结果' : '进入选课工作台'}</Button>
                {batch.selection_type_code === '04' && <Button block onClick={() => openAutomation(batch)}>本轮自动化与通知配置</Button>}
              </Space>
            </Card></Col>;
          })}</Row>
        </section>
      ))}
      {archives.length > 0 && (
        <section className="course-selection-section course-selection-archives">
          <div className="course-selection-section__title"><div><Title level={4}>课程备份</Title><Text type="secondary">进入轮次后后台静默整理本轮完整课程目录；轮次结束后保留，直到你手动删除</Text></div></div>
          <Row gutter={[16, 16]}>{archives.map(archive => {
            const underfilled = archiveUnderfilled(archive);
            const selectable = archive.selectable_count ?? (archive.courses || []).filter(course => course.eligibility_status === 'selectable').length;
            return <Col xs={24} md={12} xl={8} key={archive.archive_id}><Card className="course-selection-archive-card">
              <Space wrap>
                <Tag color={archive.archived ? 'default' : 'processing'}>{archive.archived ? '历史备份' : '持续记录中'}</Tag>
                {!archive.archived && <Tag color={archive.sync_status === 'complete' ? 'success' : archive.sync_status === 'failed' ? 'error' : 'processing'}>{archive.sync_status === 'complete' ? '完整目录已保存' : archive.sync_status === 'failed' ? '目录同步失败' : archive.sync_status === 'running' ? `正在整理 ${archive.sync_loaded || 0}/${archive.sync_total || '?'}` : '等待整理完整目录'}</Tag>}
                {archive.archived && <Tag color={archive.final_refresh_status === 'complete' ? 'success' : 'warning'}>{archive.final_refresh_status === 'complete' ? '最终人数已更新' : '未完成最终刷新'}</Tag>}
                {archive.term_name && <Tag>{archive.term_name}</Tag>}
              </Space>
              <Title level={4}>{archive.batch_name}</Title>
              <div className="course-selection-archive-stats"><span><b>{archive.course_count ?? archive.courses?.length ?? 0}</b> 个教学班</span><span><b>{selectable}</b> 个确认可选</span><span><b>{underfilled.length}</b> 个未报满</span></div>
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
      <Modal open={Boolean(automationBatch)} title={<Space><span>{`本轮自动化与通知 · ${automationBatch?.name || ''}`}</span>{automationBatch?.selection_type_code === '04' && <Button type="text" shape="circle" aria-label="了解策略投权模型" title="了解策略投权模型" icon={<QuestionCircleOutlined />} onClick={() => setModelHelpOpen(true)} />}</Space>} confirmLoading={automationSaving} onOk={saveAutomation} okText="保存配置" cancelText="取消" onCancel={() => setAutomationBatch(null)} width={700}>
        {automationLoading || !automationSettings ? <Spin /> : <div className="course-selection-automation-form">
          {automationBatch?.selection_type_code === '04' && <section className="course-selection-automation-section">
            <div><Title level={5}>策略任务的执行时间</Title><Text type="secondary">这里只决定已经在工作台中启动的策略任务何时检查。任务的创建、启动、暂停、年级人数和方案组仍在“自动任务”中管理。</Text></div>
            <label className="course-selection-setting-row"><span><b>执行方式</b><small>选择持续跟踪，或只在临近结束时集中调整。</small></span><Select value={automationSettings.strategy_schedule_mode || 'interval'} onChange={value => setAutomationSettings(s => ({ ...s, strategy_schedule_mode: value }))} options={[{ value: 'interval', label: '按间隔持续重算' }, { value: 'final_windows', label: '仅结束前 5 分钟、3 分钟执行' }]} /></label>
            {automationSettings.strategy_schedule_mode !== 'final_windows' && <>
              <label className="course-selection-setting-row"><span><b>重算间隔</b><small>每次都会读取最新人数；默认 30 分钟，最短 10 分钟，避免请求过快。</small></span><Space><InputNumber min={10} max={1440} step={5} value={Math.max(10, Math.round(Number(automationSettings.rebalance_seconds || 1800) / 60))} onChange={value => setAutomationSettings(s => ({ ...s, rebalance_seconds: Math.max(600, (Number(value) || 30) * 60) }))} /><Text>分钟</Text></Space></label>
              <label className="course-selection-setting-row"><span><b>结束前再检查一次</b><small>无论普通间隔是否到期，都在结束前 3 分钟执行。</small></span><Radio.Group className="course-selection-setting-toggle" optionType="button" buttonStyle="solid" value={automationSettings.force_final_rebalance ? 'on' : 'off'} onChange={event => setAutomationSettings(s => ({ ...s, force_final_rebalance: event.target.value === 'on' }))} options={[{ value: 'on', label: '开启' }, { value: 'off', label: '关闭' }]} /></label>
            </>}
            {automationSettings.strategy_schedule_mode === 'final_windows' && <Alert type="info" showIcon message="该模式不会定时投权" description="只对已经启动且仍在运行的策略任务生效，并在结束前 5 分钟、3 分钟分别读取最新数据并处理一次。" />}
          </section>}
          <Divider />
          <section className="course-selection-automation-section">
            <div><Title level={5}>邮件通知</Title><Text type="secondary">通知只报告状态，不会因为邮件失败重复执行选课或投权。</Text></div>
            <label className="course-selection-setting-row"><span><b>启用本轮邮件通知</b><small>关闭后下面选中的通知类型也不会发送。</small></span><Radio.Group className="course-selection-setting-toggle" optionType="button" buttonStyle="solid" value={automationSettings.mail_enabled ? 'on' : 'off'} onChange={event => setAutomationSettings(s => ({ ...s, mail_enabled: event.target.value === 'on' }))} options={[{ value: 'on', label: '开启' }, { value: 'off', label: '关闭' }]} /></label>
            <Checkbox.Group className="course-selection-notification-grid" disabled={!automationSettings.mail_enabled} value={Object.keys(automationSettings).filter(key => key.startsWith('notify_') && automationSettings[key])} onChange={keys => setAutomationSettings(s => Object.fromEntries(Object.entries(s).map(([key, value]) => [key, key.startsWith('notify_') ? keys.includes(key) : value])))} options={[
              ['notify_round_end', '轮次结束总结'], ['notify_final_rebalance', '临近结束的策略结果'], ['notify_capacity_transition', '课程从未满变为满员或超额'], ['notify_over_capacity', '课程超额达到阈值'], ['notify_underfilled_warning', '已投权课程开课风险（结束前人数不足 10）'], ['notify_grab_result', '抢课任务成功或待核验'],
            ].map(([value, label]) => ({ value, label }))} />
            <label className="course-selection-setting-row"><span><b>超额提醒阈值</b><small>例如 20% 表示容量 100、已投注人数达到 120 时提醒。</small></span><Space><InputNumber min={0} max={10} step={0.05} value={automationSettings.over_capacity_ratio} formatter={value => `${Number(value || 0) * 100}%`} parser={value => Number(String(value).replace('%', '')) / 100} onChange={value => setAutomationSettings(s => ({ ...s, over_capacity_ratio: value ?? 0.2 }))} /><Text type="secondary">超额人数 ÷ 容量</Text></Space></label>
            <Alert type={automationSettings.smtp_configured ? 'success' : 'warning'} showIcon message={automationSettings.smtp_status} description={automationSettings.smtp_configured ? '复用系统设置中的 SMTP 通道，不会在这里保存密码。' : '请先前往系统设置配置 SMTP；未配置时不会发送邮件，也不会影响自动任务。'} />
          </section>
        </div>}
      </Modal>
      <Modal open={modelHelpOpen} title="策略投权模型怎样工作？" footer={<Button type="primary" onClick={() => setModelHelpOpen(false)}>我知道了</Button>} onCancel={() => setModelHelpOpen(false)} width={760}>
        <div className="course-selection-model-help">
          <Alert type="info" showIcon message="这是辅助决策模型，不是录取概率预测" description="学校只公布容量、当前已投注人数和最终筛选规则。页面中的 SAFE、COMP、OUT 与成功率都是用于比较方案的代理值，不代表官方承诺。" />
          <section><Title level={5}>1. 读取什么数据</Title><Paragraph>每次计算前读取本轮除“全校课程查询”外的真实课程目录，并刷新方案组候选课程的已投注人数、容量、官方剩余权重、最低投权和投权步长。方案组外手动投权保持不动，只继续占用官方预算。</Paragraph></section>
          <section><Title level={5}>2. 怎样理解方案组</Title><Paragraph>每个方案组由“候选课程池 + 目标门数”组成。模型按课程代码合并同一课程的多个教学班，一门课程只占一个目标名额；同一课程的教学班按意愿值和稳定顺序选择实际提交班级，不会重复消耗目标门数。</Paragraph></section>
          <section><Title level={5}>3. 怎样选择课程</Title><Paragraph>模型先排除已确认的硬冲突组合，再按以下顺序比较可行方案：覆盖更多方案组目标名额、获得更高的课程意愿总分、提高中性竞争情景下的代理收益，最后在效果相同时使用更少权重。时间未知课程会产生风险警告，不会被当作已确认无冲突。</Paragraph></section>
          <section><Title level={5}>4. 怎样计算竞争程度</Title><Paragraph>实时策略以本次读取的官方人数分类：当前已投注人数低于容量的课程标为 SAFE；达到或超过容量的课程标为 COMP；未进入推荐组合的课程标为 OUT。模型仍根据年级人数和全市场数据计算保守、中性、激进三种终局情景，但预测只作风险参考，不会把当前未满课程改判为 COMP。</Paragraph></section>
          <section><Title level={5}>5. 怎样分配权重</Title><Paragraph>SAFE 课程固定使用官方最低权重，不会分到额外权重。其余预算通过 water-filling 分配给 COMP 课程，综合课程意愿和竞争强度，并遵守官方最低权重、步长和剩余预算。只有推荐权重与当前权重不同，系统才会按安全流程先撤回、核验，再重新投放。</Paragraph></section>
          <section><Title level={5}>6. 自动运行方式</Title><Paragraph>只有在工作台“自动任务”中明确启动的策略任务才会运行。这里的配置只决定它按间隔检查，还是仅在轮次结束前 5 分钟和 3 分钟各计算一次；关闭页面或 Linux 服务端无人打开页面时仍可继续执行。</Paragraph></section>
        </div>
      </Modal>
    </main>
  );
};

export default CourseSelectionPage;
