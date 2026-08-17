import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Card, Empty, Input, Modal, Pagination, Select, Space, Spin, Tag, Typography, message } from 'antd';
import { ArrowLeftOutlined, FilterOutlined, HistoryOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { getJwxkCatalogArchive } from '../services/api';
import TimetablePage from './TimetablePage';
import {
  courseCampusLabels, isGeneralElectiveCategory, matchesArchivedCourseFilters,
  matchesCatalogAvailability, selectionParticipantCount, selectionParticipantLabel, uniqueDisplayLabels,
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
const EMPTY_FILTERS = {
  campus: '', courseNature: '', courseCategory: '', generalElectiveCategory: '',
  department: '', startSection: '', endSection: '',
};
const option = value => ({ value, label: value });
const sortedOptions = values => uniqueDisplayLabels(values).sort((a, b) => a.localeCompare(b, 'zh-CN')).map(option);
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
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [filterDraft, setFilterDraft] = useState(EMPTY_FILTERS);
  const [weekdayDraft, setWeekdayDraft] = useState('all');
  const [filterOpen, setFilterOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState('');
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    getJwxkCatalogArchive(archiveId).then(result => {
      setArchive(result || null);
    }).catch(error => message.error(error.message || '读取课程备份失败'))
      .finally(() => setLoading(false));
  }, [archiveId]);

  const filterOptions = useMemo(() => {
    const courses = archive?.courses || [];
    return {
      campuses: sortedOptions(courses.flatMap(courseCampusLabels)),
      courseNatures: sortedOptions(courses.map(course => course.course_nature)),
      courseCategories: sortedOptions(courses.flatMap(course => [
        course.normalized_course_category, ...(course.course_categories || []), course.course_category,
      ])),
      generalElectiveCategories: sortedOptions(courses.map(course => course.general_elective_category)),
      departments: sortedOptions(courses.map(course => course.department)),
      sections: Array.from({ length: 30 }, (_, index) => ({ value: String(index + 1), label: `第 ${index + 1} 节` })),
    };
  }, [archive]);
  const categoryHasGeneralElectiveField = value => Boolean(value) && (archive?.courses || []).some(course => (
    course.general_elective_category
    && matchesArchivedCourseFilters(course, { courseCategory: value })
  ));

  const scopeOptions = useMemo(() => {
    const values = new Set((archive?.scope_options || []).map(item => item.code).filter(Boolean));
    (archive?.courses || []).flatMap(course => (
      course.source_scopes?.length ? course.source_scopes : [course.teaching_class_type]
    )).filter(Boolean).forEach(value => values.add(value));
    return [{ value: 'all', label: '全部课程来源' }, ...[...values].map(value => ({
      value,
      label: COURSE_SCOPE_LABELS[value] || '其他课程',
    }))];
  }, [archive]);

  const groups = useMemo(() => {
    const needle = keyword.trim().toLocaleLowerCase();
    return groupArchiveCourses((archive?.courses || []).filter(course => {
      if (scope !== 'all' && !new Set([
        course.teaching_class_type, ...(course.source_scopes || []),
      ]).has(scope)) return false;
      if (!matchesCatalogAvailability(course, availability, archive?.selection_type_code)) return false;
      if (!matchesArchivedCourseFilters(course, { ...filters, weekday })) return false;
      if (!needle) return true;
      return [course.course_name, course.course_code, course.teacher, course.department]
        .some(value => String(value || '').toLocaleLowerCase().includes(needle));
    }));
  }, [archive, availability, filters, keyword, scope, weekday]);
  const visibleGroups = groups.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const advancedFilterCount = [
    filters.courseNature, filters.courseCategory, filters.generalElectiveCategory,
    filters.department, filters.startSection, filters.endSection,
    weekday === 'all' ? '' : weekday,
  ].filter(Boolean).length;
  const clearFilter = key => {
    setFilters(previous => ({ ...previous, [key]: '' }));
    setPage(1);
  };
  const openFilters = () => {
    setFilterDraft(filters);
    setWeekdayDraft(weekday);
    setFilterOpen(true);
  };

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
      <Select allowClear placeholder="全部校区" value={filters.campus || undefined} options={filterOptions.campuses} onChange={value => { setFilters(previous => ({ ...previous, campus: value || '' })); setPage(1); }} />
      <Button icon={<FilterOutlined />} onClick={openFilters}>更多筛选{advancedFilterCount ? ` (${advancedFilterCount})` : ''}</Button>
    </div>
    <div className="jwxk-active-filters">
      <Text type="secondary">本地归档 {archive.courses?.length || 0} 个教学班，当前筛出 {groups.reduce((sum, group) => sum + group.class_count, 0)} 个</Text>
      {filters.campus && <Tag closable onClose={() => clearFilter('campus')}>校区 · {filters.campus}</Tag>}
      {filters.courseNature && <Tag closable onClose={() => clearFilter('courseNature')}>课程性质 · {filters.courseNature}</Tag>}
      {filters.courseCategory && <Tag closable onClose={() => clearFilter('courseCategory')}>课程类别 · {filters.courseCategory}</Tag>}
      {filters.generalElectiveCategory && <Tag closable onClose={() => clearFilter('generalElectiveCategory')}>通识类别 · {filters.generalElectiveCategory}</Tag>}
      {filters.department && <Tag closable onClose={() => clearFilter('department')}>开课单位 · {filters.department}</Tag>}
      {weekday !== 'all' && <Tag closable onClose={() => { setWeekday('all'); setPage(1); }}>星期 · 周{WEEKDAYS[Number(weekday) - 1]}</Tag>}
      {filters.startSection && <Tag closable onClose={() => clearFilter('startSection')}>开始节次 · 第 {filters.startSection} 节</Tag>}
      {filters.endSection && <Tag closable onClose={() => clearFilter('endSection')}>结束节次 · 第 {filters.endSection} 节</Tag>}
      {(advancedFilterCount || filters.campus) && <Button type="link" size="small" onClick={() => { setFilters(EMPTY_FILTERS); setWeekday('all'); setPage(1); }}>清除筛选</Button>}
    </div>
    <div className="jwxk-group-list">
      {visibleGroups.map(group => <Card key={group.group_id} className={`jwxk-course-group${expanded === group.group_id ? ' is-expanded' : ''}`} onClick={() => setExpanded(previous => previous === group.group_id ? '' : group.group_id)}>
        <div className="jwxk-course-group__head"><div><Space wrap>{uniqueDisplayLabels(group.source_tags, courseScopeLabel).map(label => <Tag key={label}>{label}</Tag>)}{uniqueDisplayLabels(group.classes.flatMap(courseCampusLabels)).map(label => <Tag color="blue" key={label}>{label}</Tag>)}</Space><Title level={4}>{group.course_name}</Title><Text type="secondary">{group.course_code || '课程代码待定'} · {group.credits || '-'} 学分 · {group.department || '开课单位待定'}{group.classes[0]?.course_nature ? ` · ${group.classes[0].course_nature}` : ''}{group.classes[0]?.normalized_course_category || group.classes[0]?.course_category ? ` · ${group.classes[0].normalized_course_category || group.classes[0].course_category}` : ''}</Text></div><Badge count={group.class_count} /></div>
        <div className="jwxk-course-group__stats"><span>{group.classes.filter(item => item.eligibility_status === 'selectable').length} 个确认可选</span><span>{group.classes.filter(item => selectionParticipantCount(item, archive.selection_type_code) != null && Number(item.capacity || 0) > selectionParticipantCount(item, archive.selection_type_code)).length} 个{archive.selection_type_code === '04' ? '当时未超容量' : '未报满'}</span><b>{expanded === group.group_id ? '收起教学班' : '查看教学班'}</b></div>
        {expanded === group.group_id && <div className="jwxk-inline-classes" onClick={event => event.stopPropagation()}>{group.classes.map(course => <article className={`jwxk-inline-class${preview?.class_id === course.class_id ? ' is-previewing' : ''}`} key={course.class_id}>
          <div className="jwxk-inline-class__summary"><strong>{course.teacher || '教师待定'}</strong><span>{course.official_schedule || '时间待定'}</span><small>{courseCampusLabels(course).join('、') || '校区待定'} · {selectionParticipantLabel(course, archive.selection_type_code)} {selectionParticipantCount(course, archive.selection_type_code) ?? '-'} / 容量 {course.capacity ?? '-'}</small></div>
          <Space wrap className="jwxk-inline-class__states">{course.eligibility_status === 'selectable' && <Tag color="success">本轮可选</Tag>}{course.full && <Tag>已满</Tag>}{course.conflict && <Tag color="error">官方冲突</Tag>}</Space>
          <Space className="jwxk-inline-class__actions"><Button size="small" onClick={() => setPreview(previous => previous?.class_id === course.class_id ? null : course)}>{preview?.class_id === course.class_id ? '取消课表预览' : '在课表中预览'}</Button></Space>
        </article>)}<Button className="jwxk-collapse-classes" type="text" onClick={() => setExpanded('')}>收起教学班</Button></div>}
      </Card>)}
      {!visibleGroups.length && <Empty description={scope === 'ALLKC'
        ? '这份历史备份中没有已保存的全校课程查询结果'
        : '当前条件下没有课程'} />}
    </div>
    {groups.length > PAGE_SIZE && <Pagination current={page} pageSize={PAGE_SIZE} total={groups.length} showSizeChanger={false} onChange={setPage} />}
    <Modal open={filterOpen} title="更多本地筛选" okText="应用筛选" cancelText="取消" onCancel={() => setFilterOpen(false)} onOk={() => { setFilters(filterDraft); setWeekday(weekdayDraft); setPage(1); setFilterOpen(false); }} footer={(_, { OkBtn, CancelBtn }) => <><Button onClick={() => { setFilterDraft(EMPTY_FILTERS); setWeekdayDraft('all'); }}>重置</Button><CancelBtn /><OkBtn /></>}>
      <Alert type="info" showIcon message="筛选仅使用这份归档中已保存的数据，不会访问学校系统。" />
      <div className="jwxk-filter-grid jwxk-archive-filter-grid">
        <label>课程性质<Select allowClear placeholder="全部课程性质" value={filterDraft.courseNature || undefined} options={filterOptions.courseNatures} onChange={value => setFilterDraft(previous => ({ ...previous, courseNature: value || '' }))} /></label>
        <label>课程类别<Select allowClear placeholder="全部课程类别" value={filterDraft.courseCategory || undefined} options={filterOptions.courseCategories} onChange={value => setFilterDraft(previous => ({ ...previous, courseCategory: value || '', generalElectiveCategory: (isGeneralElectiveCategory(value) || categoryHasGeneralElectiveField(value)) ? previous.generalElectiveCategory : '' }))} /></label>
        {(isGeneralElectiveCategory(filterDraft.courseCategory) || categoryHasGeneralElectiveField(filterDraft.courseCategory)) && <label>通识选修课类别<Select allowClear placeholder="全部通识类别" value={filterDraft.generalElectiveCategory || undefined} options={filterOptions.generalElectiveCategories} onChange={value => setFilterDraft(previous => ({ ...previous, generalElectiveCategory: value || '' }))} /></label>}
        <label>星期<Select value={weekdayDraft} options={[{ value: 'all', label: '全部星期' }, ...WEEKDAYS.map((label, index) => ({ value: String(index + 1), label: `周${label}` }))]} onChange={setWeekdayDraft} /></label>
        <label>开课单位<Select showSearch allowClear optionFilterProp="label" placeholder="全部开课单位" value={filterDraft.department || undefined} options={filterOptions.departments} onChange={value => setFilterDraft(previous => ({ ...previous, department: value || '' }))} /></label>
        <label>开始节次<Select allowClear placeholder="不限" value={filterDraft.startSection || undefined} options={filterOptions.sections} onChange={value => setFilterDraft(previous => ({ ...previous, startSection: value || '' }))} /></label>
        <label>结束节次<Select allowClear placeholder="不限" value={filterDraft.endSection || undefined} options={filterOptions.sections} onChange={value => setFilterDraft(previous => ({ ...previous, endSection: value || '' }))} /></label>
      </div>
    </Modal>
  </main>;
}
