import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Empty, Input, Modal, Select, Space, Spin, Tag, Typography, message, Badge,
} from 'antd';
import {
  ArrowLeftOutlined, BookOutlined,
  ReloadOutlined, RollbackOutlined, SearchOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import TimetablePage from './TimetablePage';
import SchedulePreviewControls from '../components/SchedulePreviewControls';
import { useCachedResource } from '../resources/ResourceStore';
import { deselectOfficialCourse, getOfficialWithdrawalCourses } from '../services/api';
import { collectAcademicPlanDeficits, getAcademicRuleDeficitText } from '../utils/academicReport';
import './CourseSelectionPage.css';

const { Title, Text } = Typography;

const formatDate = value => {
  const text = String(value || '').trim();
  return text ? text.replace(' ', ' · ') : '时间待确认';
};

const formatNumber = value => {
  if (value == null || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? String(number).replace(/\.0+$/, '') : String(value);
};

const groupKey = course => course.course_code || course.course_name || course.wid;
const weekdayNames = ['一', '二', '三', '四', '五', '六', '日'];
const scheduleSummary = course => {
  const meetings = course.schedules || [];
  if (!meetings.length) return course.schedule || '上课时间待确认';
  const first = meetings[0];
  const weeks = first.weeks || [];
  const consecutive = weeks.every((week, index) => index === 0 || week === weeks[index - 1] + 1);
  const weekText = weeks.length === 1 ? `${weeks[0]}周`
    : consecutive && weeks.length > 1 ? `${weeks[0]}-${weeks[weeks.length - 1]}周`
      : `${weeks.length}个周次`;
  const day = weekdayNames[Number(first.weekday) - 1];
  const section = first.start_section === first.end_section
    ? `${first.start_section}节` : `${first.start_section}-${first.end_section}节`;
  return [weekText, day && `周${day}`, first.start_section && section,
    meetings.length > 1 && `另有${meetings.length - 1}段`].filter(Boolean).join(' · ');
};

const OfficialWithdrawalWorkspacePage = () => {
  const navigate = useNavigate();
  const academicReportResource = useCachedResource('academic-report', { autoRefresh: false }) || {};
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('all');
  const [nature, setNature] = useState('all');
  const [category, setCategory] = useState('all');
  const [expandedGroups, setExpandedGroups] = useState({});
  const [detailCourse, setDetailCourse] = useState(null);
  const [previewWid, setPreviewWid] = useState('');
  const [activeGapId, setActiveGapId] = useState('');
  const [submitting, setSubmitting] = useState('');
  const catalogSectionRef = useRef(null);
  const scheduleRef = useRef(null);
  const courseCardRefs = useRef(new Map());
  const courseClassRefs = useRef(new Map());

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setPayload(await getOfficialWithdrawalCourses());
    } catch (reason) {
      setPayload(null);
      setError(reason?.response?.data?.detail || reason?.message || '官方退课入口读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const courseMetadata = useMemo(() => {
    const result = new Map();
    const walk = (nodes, path = []) => (nodes || []).forEach(node => {
      const nextPath = [...path, node.name].filter(Boolean);
      (node.courses || []).forEach(course => {
        const code = String(course.course_code || course.code || '').trim().toUpperCase();
        if (!code) return;
        const previous = result.get(code) || {};
        result.set(code, {
          ...previous,
          course_category: previous.course_category || course.course_category || nextPath.join(' > '),
          course_nature: previous.course_nature || course.course_nature || '',
          academic_path: previous.academic_path || nextPath.join(' > '),
        });
      });
      walk(node.children, nextPath);
    });
    walk(academicReportResource.data?.categories || []);
    return result;
  }, [academicReportResource.data]);
  const courses = useMemo(() => (payload?.courses || []).map(course => {
    const metadata = courseMetadata.get(String(course.course_code || '').trim().toUpperCase()) || {};
    return {
      ...course,
      course_category: course.course_category || metadata.course_category || '',
      course_nature: course.course_nature || metadata.course_nature || '',
      academic_path: metadata.academic_path || '',
    };
  }), [courseMetadata, payload]);
  const previewedCourse = courses.find(course => course.wid === previewWid);
  const filtered = useMemo(() => courses.filter(course => {
    const term = keyword.trim().toLocaleLowerCase();
    if (term && ![
      course.course_name, course.course_code, course.course_serial,
      course.teacher, course.department, course.schedule,
    ].some(value => String(value || '').toLocaleLowerCase().includes(term))) return false;
    if (status === 'available' && !course.can_withdraw) return false;
    if (status === 'unavailable' && course.can_withdraw) return false;
    if (nature !== 'all' && course.course_nature !== nature) return false;
    if (category !== 'all' && course.course_category !== category) return false;
    return true;
  }), [courses, keyword, status, nature, category]);
  const groups = useMemo(() => {
    const result = new Map();
    filtered.forEach(course => {
      const key = groupKey(course);
      if (!result.has(key)) result.set(key, {
        key,
        course_name: course.course_name,
        course_code: course.course_code,
        course_nature: course.course_nature,
        course_category: course.course_category,
        credits: course.credits,
        source_label: course.source_label,
        rows: [],
      });
      result.get(key).rows.push(course);
    });
    return [...result.values()];
  }, [filtered]);
  const optionsFor = field => [
    { value: 'all', label: '全部' },
    ...[...new Set(courses.map(item => item[field]).filter(Boolean))].map(value => ({ value, label: value })),
  ];
  const planGaps = useMemo(() => collectAcademicPlanDeficits(
    academicReportResource.data?.categories || [], undefined, { actionableOnly: true },
  ), [academicReportResource.data]);
  const academicCourseIndex = useMemo(() => {
    const result = new Map();
    const walk = (nodes, path = []) => (nodes || []).forEach(node => {
      const nextPath = [...path, node.name].filter(Boolean);
      (node.courses || []).forEach(course => {
        const code = String(course.course_code || course.code || '').trim().toUpperCase();
        if (!code) return;
        const rows = result.get(code) || [];
        rows.push({ course, node, path: nextPath.join(' > ') });
        result.set(code, rows);
      });
      walk(node.children, nextPath);
    });
    walk(academicReportResource.data?.categories || []);
    return result;
  }, [academicReportResource.data]);
  const withdrawalImpacts = course => (academicCourseIndex.get(String(course.course_code || '').trim().toUpperCase()) || [])
    .filter(item => item.course.is_selected && !item.course.is_passed)
    .map(item => {
      const credit = Number(item.course.credit || course.credits || 0);
      const remaining = Number(item.node.remaining_credits || 0);
      const projectedRemaining = remaining + credit;
      return {
        path: item.path || item.node.name || '对应培养计划类别',
        credit,
        remaining,
        projectedRemaining,
        becomesDeficit: credit > 0 && projectedRemaining > 0,
      };
    });

  const refundableWeight = course => {
    if (!course.can_withdraw || course.penalty_weight == null || course.weight == null) return null;
    const weight = Number(course.weight);
    const penalty = Number(course.penalty_weight);
    return Number.isFinite(weight) && Number.isFinite(penalty)
      ? Math.max(0, weight - penalty) : null;
  };

  const cancelPreview = course => {
    setPreviewWid('');
    (courseClassRefs.current.get(course.wid)
      || courseCardRefs.current.get(groupKey(course))
      || catalogSectionRef.current)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  };

  const previewCourse = course => {
    if (previewWid === course.wid) {
      cancelPreview(course);
      return;
    }
    if (!course.schedules?.length) {
      message.info('官方未提供可定位的完整上课时间，暂无法在课表中预览');
      return;
    }
    setPreviewWid(course.wid);
    scheduleRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  };

  const clearFilters = () => {
    setKeyword('');
    setStatus('all');
    setNature('all');
    setCategory('all');
    setActiveGapId('');
  };

  const confirmWithdraw = course => {
    const impacts = withdrawalImpacts(course);
    const refund = refundableWeight(course);
    Modal.confirm({
      title: `确认退课“${course.course_name || course.course_code || '这门课程'}”？`,
      icon: <RollbackOutlined />,
      width: 520,
      content: (
        <div className="official-withdrawal-confirm">
          <p>这是教务系统独立退课管理，不受普通选课轮次控制。提交后课程会从官方已选记录移除。</p>
          {refund != null && <Alert type="info" showIcon message={`预计返还 ${formatNumber(refund)} 权重`} description="按当前权重减去官方阶段扣除权重估算，以教务系统提交结果为准。" />}
          {course.penalty_label && <p>当前退课阶段：{course.penalty_label}{course.penalty_weight != null ? `，预计扣除 ${course.penalty_weight} 权重` : ''}。</p>}
          {impacts.filter(item => item.becomesDeficit).map(item => <Alert key={item.path} type="warning" showIcon message={`退课后“${item.path}”预计会少 ${formatNumber(item.credit)} 学分`} description="请确认是否仍满足培养计划要求。" />)}
          {impacts.length > 0 && !impacts.some(item => item.becomesDeficit) && <p>培养计划中当前已记录本课程；退课后相关类别的已修/已选学分会重新计算。</p>}
          <p>请确认已经结合课表和培养计划核对退课影响。</p>
        </div>
      ),
      okText: '确认退课',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setSubmitting(course.wid);
        try {
          await deselectOfficialCourse({ term_code: payload?.term_code, wid: course.wid });
          message.success('官方退课请求已提交');
          await load();
        } catch (reason) {
          message.error(reason?.response?.data?.detail || reason?.message || '官方退课失败，请刷新后确认');
          throw reason;
        } finally {
          setSubmitting('');
        }
      },
    });
  };

  const renderClass = course => {
    const states = course.can_withdraw
      ? <Tag color="success">可退课</Tag>
      : <Tag>不可退课</Tag>;
    const highlighted = previewWid === course.wid;
    const refund = refundableWeight(course);
    return (
      <article
        className={`jwxk-inline-class${course.can_withdraw ? '' : ' is-disabled'}${highlighted ? ' is-previewing' : ''}`}
        key={course.wid}
        ref={element => {
          if (element) courseClassRefs.current.set(course.wid, element);
          else courseClassRefs.current.delete(course.wid);
        }}
      >
        <div className="jwxk-inline-class__summary">
          <strong>{course.teacher || '教师待定'}</strong>
          <span title={course.schedule || undefined}>{scheduleSummary(course)}</span>
          {!course.can_withdraw && <small className="official-withdrawal-workspace__reason">{course.unavailable_reason || '官方暂不允许'}</small>}
        </div>
        <div className="official-withdrawal-workspace__class-controls">
          <Space wrap className="jwxk-inline-class__states">
            {states}
            {course.can_withdraw && <Tag color="processing">{refund != null ? `预计返还 ${formatNumber(refund)} 权重` : '返还权重待确认'}</Tag>}
          </Space>
          <Space wrap className="jwxk-inline-class__actions">
            <Button size="small" onClick={() => setDetailCourse(course)}>查看详情</Button>
            <Button size="small" disabled={!course.schedules?.length} onClick={() => previewCourse(course)}>{highlighted ? '取消课表预览' : '在课表中预览'}</Button>
            <Button danger size="small" disabled={!course.can_withdraw} loading={submitting === course.wid} onClick={() => confirmWithdraw(course)}>退课</Button>
          </Space>
        </div>
      </article>
    );
  };

  return (
    <main className="course-selection-page jwxk-workspace official-withdrawal-workspace">
      <header className="jwxk-workspace-header">
        <Button className="jwxk-header-action jwxk-header-back" icon={<ArrowLeftOutlined />} onClick={() => navigate('/course-selection')}>返回轮次</Button>
        <div>
          <Title level={3}>退课工作台</Title>
          <Space wrap><Text type="secondary">{payload?.term_name || payload?.term_code || '当前学期'} · 官方实时数据</Text><Tag color="gold">退课轮次</Tag></Space>
        </div>
        <Button className="jwxk-header-action jwxk-header-refresh" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
      </header>
      {error && <Alert className="official-withdrawal-workspace__error" type="warning" showIcon message="退课入口暂不可用" description={error} action={<Button size="small" onClick={load}>重试</Button>} />}
      {loading && !payload ? <div className="official-withdrawal-workspace__loading"><Spin /> 正在读取官方退课列表…</div> : !payload && error ? null : (
        <>
          <section className="jwxk-live-schedule official-withdrawal-workspace__timetable" ref={scheduleRef}>
            <div className="jwxk-live-schedule__head">
              <div><Title level={4}>选课课表</Title><Text type="secondary">用于核对课程时间和退课影响。</Text></div>
            </div>
            <SchedulePreviewControls
              courses={previewedCourse ? [previewedCourse] : []}
              courseKey={course => course.wid}
              onCancel={cancelPreview}
            />
            {payload?.term_code ? <TimetablePage
              embedded
              preferredTermCode={payload.term_code}
              initialViewMode="term"
              presentation="selection"
              overlayCourses={previewedCourse?.schedules || []}
            /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前学期信息待确认" />}
          </section>
          <div className="jwxk-catalog-layout is-plan-managing official-withdrawal-workspace__catalog-layout">
            <section ref={catalogSectionRef}>
              <div className="jwxk-search-row">
                <Input.Search allowClear enterButton="搜索" prefix={<SearchOutlined />} value={keyword} onChange={event => { setKeyword(event.target.value); setActiveGapId(''); }} onSearch={value => { setKeyword(value.trim()); setActiveGapId(''); }} placeholder="输入课程名、课程号、教师或安排" />
                <Select value={status} onChange={setStatus} options={[{ value: 'all', label: '全部状态' }, { value: 'available', label: '当前可退' }, { value: 'unavailable', label: '暂不可退' }]} />
                <Select value={nature} onChange={setNature} options={optionsFor('course_nature')} placeholder="课程性质" />
                <Select value={category} onChange={setCategory} options={optionsFor('course_category')} placeholder="课程类别" />
                <Button onClick={clearFilters}>清除筛选</Button>
              </div>
              {(keyword || status !== 'all' || nature !== 'all' || category !== 'all') && (
                <div className="jwxk-active-filters">
                  {keyword && <Tag closable onClose={() => { setKeyword(''); setActiveGapId(''); }}>关键词 · {keyword}</Tag>}
                  {status !== 'all' && <Tag closable onClose={() => setStatus('all')}>状态 · {status === 'available' ? '当前可退' : '暂不可退'}</Tag>}
                  {nature !== 'all' && <Tag closable onClose={() => setNature('all')}>性质 · {nature}</Tag>}
                  {category !== 'all' && <Tag closable onClose={() => setCategory('all')}>类别 · {category}</Tag>}
                </div>
              )}
              <div className="jwxk-group-list">
                {courses.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={payload?.entry_status === 'closed' ? '教务系统当前未开放退课入口' : '退课入口已开放，但当前没有课程'} /> : groups.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选下没有课程" /> : groups.map(group => {
                  const expanded = Boolean(expandedGroups[group.key]);
                  const available = group.rows.filter(item => item.can_withdraw).length;
                  const hasAlternatives = group.rows.length > 1;
                  return <Card
                    key={group.key}
                    ref={element => {
                      if (element) courseCardRefs.current.set(group.key, element);
                      else courseCardRefs.current.delete(group.key);
                    }}
                    className={`jwxk-course-group${hasAlternatives ? ' has-alternatives' : ' is-single'}${expanded ? ' is-expanded' : ''}`}
                    onClick={hasAlternatives ? () => setExpandedGroups(previous => ({ ...previous, [group.key]: !previous[group.key] })) : undefined}
                  >
                    <div className="jwxk-course-group__head">
                      <div className="jwxk-course-group__main">
                        <Space wrap>
                          {group.source_label && <Tag color="blue">{group.source_label}</Tag>}
                          {group.course_category && <Tag color="geekblue">类别 · {group.course_category}</Tag>}
                          {group.course_nature && <Tag>性质 · {group.course_nature}</Tag>}
                        </Space>
                        <div className="jwxk-course-group__title-row">
                          <Title level={4}>{group.course_name || '未提供课程名称'}</Title>
                          <div className="jwxk-course-group__facts">
                            <span>学分 <b>{formatNumber(group.credits)}</b></span>
                          </div>
                        </div>
                        <Text type="secondary">{group.course_code || '课程代码待定'}</Text>
                      </div>
                      {hasAlternatives && <Badge count={group.rows.length} overflowCount={99} />}
                    </div>
                    {hasAlternatives && <div className="jwxk-course-group__stats">
                      <span>{available} 个可退 · {group.rows.length - available} 个暂不可退</span>
                      <Button type="link" onClick={event => { event.stopPropagation(); setExpandedGroups(previous => ({ ...previous, [group.key]: !previous[group.key] })); }} aria-expanded={expanded}>{expanded ? '收起教学班' : '比较教学班'}</Button>
                    </div>}
                    {(!hasAlternatives || expanded) && <div className="jwxk-inline-classes" onClick={event => event.stopPropagation()}>{group.rows.map(renderClass)}{hasAlternatives && <Button className="jwxk-collapse-classes" type="text" onClick={() => setExpandedGroups(previous => ({ ...previous, [group.key]: false }))}>收起教学班</Button>}</div>}
                  </Card>;
                })}
              </div>
            </section>
            <aside className="jwxk-plan-aside" aria-label="培养计划缺口">
              <section className="jwxk-plan-gap-panel">
                <div className="jwxk-plan-gap-panel__head"><span><BookOutlined /><b>培养计划缺口</b></span>{academicReportResource.data && <Tag>{planGaps.length} 项</Tag>}</div>
                <small className="jwxk-plan-gap-program">退课目录不改变培养计划，仅用于辅助核对退课后的剩余要求。</small>
                {academicReportResource.loading && !academicReportResource.data && <div className="jwxk-plan-gap-loading"><Spin size="small" /> 读取培养计划缓存…</div>}
                {planGaps.map(gap => <button
                  type="button"
                  className={`jwxk-plan-gap${activeGapId === (gap.wid || gap.path || gap.name) ? ' is-active' : ''}`}
                  key={gap.wid || gap.path || gap.name}
                  onClick={() => {
                    const gapId = gap.wid || gap.path || gap.name;
                    const first = gap.unfinished_courses?.[0];
                    setActiveGapId(gapId);
                    setKeyword(first?.course_code || first?.course_name || gap.name || '');
                    window.setTimeout(() => catalogSectionRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }), 40);
                  }}
                >
                  <span><strong>{gap.name}</strong><Tag color={gap.requirement_type === 'elective' ? 'blue' : 'default'}>{gap.requirement_type === 'elective' ? '选修' : gap.requirement_type === 'required' ? '必修' : '综合'}</Tag></span>
                  <b>{getAcademicRuleDeficitText(gap)}</b>
                  {gap.unfinished_courses?.length > 0 && <small>待修：{gap.unfinished_courses.slice(0, 2).map(item => item.course_name).filter(Boolean).join('、')}{gap.unfinished_courses.length > 2 ? ' 等' : ''}</small>}
                </button>)}
                {academicReportResource.data && !planGaps.length && <div className="jwxk-plan-gap-empty">当前没有待修缺口</div>}
                {!academicReportResource.data && !academicReportResource.loading && <div className="jwxk-plan-gap-empty">尚未读取培养计划，退课目录仍可使用。</div>}
              </section>
            </aside>
          </div>
        </>
      )}
      <Modal
        className="jwxk-catalog-detail-modal"
        title={`${detailCourse?.course_name || '教学班详情'} · ${detailCourse?.teacher || '教师待定'}`}
        open={Boolean(detailCourse)}
        onCancel={() => setDetailCourse(null)}
        footer={null}
        width={720}
        destroyOnHidden
      >
        {detailCourse && <Descriptions bordered column={{ xs: 1, sm: 2 }} size="small">
          <Descriptions.Item label="课程号">{detailCourse.course_code || '待定'}</Descriptions.Item>
          <Descriptions.Item label="课序号">{detailCourse.course_serial || '待定'}</Descriptions.Item>
          <Descriptions.Item label="教师">{detailCourse.teacher || '待定'}</Descriptions.Item>
          <Descriptions.Item label="学分">{formatNumber(detailCourse.credits)}</Descriptions.Item>
          <Descriptions.Item label="课程性质">{detailCourse.course_nature || '待定'}</Descriptions.Item>
          <Descriptions.Item label="课程类别">{detailCourse.course_category || '待定'}</Descriptions.Item>
          <Descriptions.Item label="上课安排" span={2}>{detailCourse.schedule || '待定'}</Descriptions.Item>
          <Descriptions.Item label="开课单位" span={2}>{detailCourse.department || '待定'}</Descriptions.Item>
          <Descriptions.Item label="课程来源">{detailCourse.source_label || '待定'}</Descriptions.Item>
          <Descriptions.Item label="原始权重">{formatNumber(detailCourse.weight)}</Descriptions.Item>
          <Descriptions.Item label="退课时间" span={2}>{formatDate(detailCourse.withdrawal_start_at)} 至 {formatDate(detailCourse.withdrawal_end_at)}</Descriptions.Item>
          <Descriptions.Item label="当前状态" span={2}>{detailCourse.can_withdraw ? '当前可退课' : detailCourse.unavailable_reason || '官方当前不允许退课'}</Descriptions.Item>
          {detailCourse.can_withdraw && <Descriptions.Item label="阶段扣除">{detailCourse.penalty_label || '待确认'}{detailCourse.penalty_weight != null ? ` · ${formatNumber(detailCourse.penalty_weight)} 权重` : ''}</Descriptions.Item>}
          {refundableWeight(detailCourse) != null && <Descriptions.Item label="预计返还">{formatNumber(refundableWeight(detailCourse))} 权重</Descriptions.Item>}
        </Descriptions>}
      </Modal>
    </main>
  );
};

export default OfficialWithdrawalWorkspacePage;
