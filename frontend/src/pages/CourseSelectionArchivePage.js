import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Card, Empty, Input, Pagination, Select, Space, Spin, Tag, Typography, message } from 'antd';
import { ArrowLeftOutlined, HistoryOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { getJwxkCatalogArchives } from '../services/api';
import TimetablePage from './TimetablePage';
import {
  matchesCatalogAvailability, selectionParticipantCount, selectionParticipantLabel,
} from '../utils/jwxkSchedule';
import './CourseSelectionPage.css';

const COURSE_SCOPE_LABELS = {
  TJKC: '任务推荐班课程', FANKC: '培养方案内课', FAWKC: '培养方案外课程',
  XGKC: '通识选修课', CXKC: '重修课程', TYKC: '体育项目', FXKC: '辅修课程',
  ALLKC: '全校课程查询', BYKC: '本研课程', ZYNKC: '专业内课程', ROUND: '本轮课程',
};

const { Text, Title } = Typography;
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];
const PAGE_SIZE = 20;
const courseScopeLabel = value => COURSE_SCOPE_LABELS[value]
  || (/^[A-Z0-9_]+$/.test(String(value || '')) ? '其他课程' : value)
  || '其他课程';

const groupArchiveCourses = courses => {
  const groups = new Map();
  (courses || []).forEach(course => {
    const key = course.course_code || `${course.course_name}|${course.credits}|${course.department}`;
    if (!groups.has(key)) groups.set(key, {
      group_id: key,
      course_code: course.course_code || '',
      course_name: course.course_name || '未命名课程',
      credits: course.credits || '',
      department: course.department || '',
      source_tags: new Set(),
      classes: [],
    });
    const group = groups.get(key);
    (course.source_tags || []).forEach(tag => group.source_tags.add(tag));
    group.classes.push(course);
  });
  return [...groups.values()].map(group => ({
    ...group,
    source_tags: [...group.source_tags],
    class_count: group.classes.length,
  }));
};

const archiveOverlay = course => (course?.schedules || []).map((meeting, index) => ({
  ...meeting,
  id: `jwxk-archive-${course.class_id}-${index}`,
  meeting_id: `jwxk-archive-${course.class_id}-${index}`,
  source_id: course.class_id,
  course_name: course.course_name,
  course_code: course.course_code,
  teaching_class_id: course.class_id,
  weekday: Number(meeting.weekday || 0),
  start_section: Number(meeting.start_section || 0),
  end_section: Number(meeting.end_section || meeting.start_section || 0),
  weeks: Array.isArray(meeting.weeks) ? meeting.weeks.map(Number) : [],
  recurrence_unknown: Boolean(meeting.recurrence_unknown || !meeting.weeks?.length),
  teachers: course.teacher ? [course.teacher] : [],
  course_type: '历史轮次预览',
  tags: ['历史备份'],
  color: '#64748b',
  layer: 'preview',
}));

export default function CourseSelectionArchivePage() {
  const { archiveId } = useParams();
  const navigate = useNavigate();
  const [archive, setArchive] = useState(null);
  const [loading, setLoading] = useState(true);
  const [keywordDraft, setKeywordDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  const [scope, setScope] = useState('all');
  const [availability, setAvailability] = useState('all');
  const [weekday, setWeekday] = useState('all');
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState('');
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    getJwxkCatalogArchives().then(result => {
      const found = (result.archives || []).find(item => item.archive_id === archiveId);
      setArchive(found || null);
    }).catch(error => message.error(error.message || '读取课程备份失败'))
      .finally(() => setLoading(false));
  }, [archiveId]);

  const scopeOptions = useMemo(() => {
    const values = new Set((archive?.courses || []).map(course => course.teaching_class_type).filter(Boolean));
    return [{ value: 'all', label: '全部课程来源' }, ...[...values].map(value => ({
      value,
      label: COURSE_SCOPE_LABELS[value] || '其他课程',
    }))];
  }, [archive]);

  const groups = useMemo(() => {
    const needle = keyword.trim().toLocaleLowerCase();
    return groupArchiveCourses((archive?.courses || []).filter(course => {
      if (scope !== 'all' && course.teaching_class_type !== scope) return false;
      if (!matchesCatalogAvailability(course, availability, archive?.selection_type_code)) return false;
      if (weekday !== 'all' && !(course.schedules || []).some(item => String(item.weekday) === weekday)) return false;
      if (!needle) return true;
      return [course.course_name, course.course_code, course.teacher, course.department]
        .some(value => String(value || '').toLocaleLowerCase().includes(needle));
    }));
  }, [archive, availability, keyword, scope, weekday]);
  const visibleGroups = groups.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  if (loading) return <main className="course-selection-page"><Spin tip="读取历史轮次课程…" /></main>;
  if (!archive) return <main className="course-selection-page"><Alert type="error" showIcon message="课程备份不存在或已经删除" action={<Button onClick={() => navigate('/course-selection')}>返回批次</Button>} /></main>;

  return <main className="course-selection-page jwxk-workspace">
    <header className="jwxk-workspace-header">
      <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/course-selection')}>批次</Button>
      <div><Title level={3}>{archive.batch_name}</Title><Text type="secondary">{archive.term_name || archive.term_code} · 历史课程备份</Text></div>
      <Tag icon={<HistoryOutlined />}>只读</Tag>
    </header>
    <Alert type="info" showIcon message="这是轮次结束时保留的课程数据，可检索、筛选和预览课表，但不提供选课、退课、投权或自动任务操作。" />
    <section className="jwxk-live-schedule">
      <div className="jwxk-live-schedule__head">
        <div><Title level={4}>历史轮次课表预览</Title><Text type="secondary">选择教学班后叠加到对应学期的课表中比较时间。</Text></div>
        {preview && <Button size="small" onClick={() => setPreview(null)}>取消“{preview.course_name}”的课表预览</Button>}
      </div>
      <TimetablePage embedded preferredTermCode={archive.term_code} initialViewMode="term" overlayCourses={archiveOverlay(preview)} presentation="selection" />
    </section>
    <div className="jwxk-search-row jwxk-archive-search">
      <Input.Search allowClear enterButton="搜索" prefix={<SearchOutlined />} value={keywordDraft} onChange={event => setKeywordDraft(event.target.value)} onSearch={value => { setKeyword(value.trim()); setPage(1); }} placeholder="输入课程名称、代码、教师或单位" />
      <Select value={scope} options={scopeOptions} onChange={value => { setScope(value); setPage(1); }} />
      <Select value={availability} onChange={value => { setAvailability(value); setPage(1); }} options={[{ value: 'all', label: '全部状态' }, { value: 'selectable', label: '本轮可选' }, { value: 'available', label: archive.selection_type_code === '04' ? '当时未超容量' : '未报满' }, { value: 'conflict_free', label: '官方无冲突' }, { value: 'selected', label: '当时已选' }]} />
      <Select value={weekday} onChange={value => { setWeekday(value); setPage(1); }} options={[{ value: 'all', label: '全部星期' }, ...WEEKDAYS.map((label, index) => ({ value: String(index + 1), label: `周${label}` }))]} />
    </div>
    <div className="jwxk-group-list">
      {visibleGroups.map(group => <Card key={group.group_id} className={`jwxk-course-group${expanded === group.group_id ? ' is-expanded' : ''}`} onClick={() => setExpanded(previous => previous === group.group_id ? '' : group.group_id)}>
        <div className="jwxk-course-group__head"><div><Space wrap>{group.source_tags.map(tag => <Tag key={tag}>{courseScopeLabel(tag)}</Tag>)}</Space><Title level={4}>{group.course_name}</Title><Text type="secondary">{group.course_code || '课程代码待定'} · {group.credits || '-'} 学分 · {group.department || '开课单位待定'}</Text></div><Badge count={group.class_count} /></div>
        <div className="jwxk-course-group__stats"><span>{group.classes.filter(item => item.eligibility_status === 'selectable').length} 个确认可选</span><span>{group.classes.filter(item => selectionParticipantCount(item, archive.selection_type_code) != null && Number(item.capacity || 0) > selectionParticipantCount(item, archive.selection_type_code)).length} 个{archive.selection_type_code === '04' ? '当时未超容量' : '未报满'}</span><b>{expanded === group.group_id ? '收起教学班' : '查看教学班'}</b></div>
        {expanded === group.group_id && <div className="jwxk-inline-classes" onClick={event => event.stopPropagation()}>{group.classes.map(course => <article className={`jwxk-inline-class${preview?.class_id === course.class_id ? ' is-previewing' : ''}`} key={course.class_id}>
          <div className="jwxk-inline-class__summary"><strong>{course.teacher || '教师待定'}</strong><span>{course.official_schedule || '时间待定'}</span><small>{selectionParticipantLabel(course, archive.selection_type_code)} {selectionParticipantCount(course, archive.selection_type_code) ?? '-'} / 容量 {course.capacity ?? '-'}</small></div>
          <Space wrap className="jwxk-inline-class__states">{course.eligibility_status === 'selectable' && <Tag color="success">本轮可选</Tag>}{course.full && <Tag>已满</Tag>}{course.conflict && <Tag color="error">官方冲突</Tag>}</Space>
          <Space className="jwxk-inline-class__actions"><Button size="small" onClick={() => setPreview(previous => previous?.class_id === course.class_id ? null : course)}>{preview?.class_id === course.class_id ? '取消课表预览' : '在课表中预览'}</Button></Space>
        </article>)}<Button className="jwxk-collapse-classes" type="text" onClick={() => setExpanded('')}>收起教学班</Button></div>}
      </Card>)}
      {!visibleGroups.length && <Empty description="当前条件下没有课程" />}
    </div>
    {groups.length > PAGE_SIZE && <Pagination current={page} pageSize={PAGE_SIZE} total={groups.length} showSizeChanger={false} onChange={setPage} />}
  </main>;
}
