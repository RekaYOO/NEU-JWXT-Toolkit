import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Drawer, Empty, Skeleton, Spin, Tag, Typography, message } from 'antd';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { getCourseOutlineOverview, getCourseOutlineSections } from '../services/api';
import { downloadCourseOutlineHtml } from '../utils/courseOutlineExport';
import './CourseOutlineDrawer.css';

const { Paragraph, Text, Title } = Typography;
const GROUPS = [
  { key: 'teaching', label: '教学内容' },
  { key: 'assessment', label: '考核与评价' },
  { key: 'governance', label: '编制与附件' },
];

const TextValue = ({ value }) => <Paragraph className="outline-paragraph">{value || <Text type="secondary">暂无内容</Text>}</Paragraph>;

const PresentedSection = ({ section }) => {
  if (!section) return null;
  if (section.kind === 'table') return (
    <article className="outline-section-card">
      <Title level={4}>{section.title}</Title>
      <div className="outline-table-wrap"><table className="outline-data-table"><thead><tr>{section.columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>{section.rows.map((row, index) => <tr key={index}>{section.columns.map(column => <td key={column}>{row[column] || '—'}</td>)}</tr>)}</tbody>
      </table></div>
    </article>
  );
  if (section.kind === 'attachments') return (
    <article className="outline-section-card"><Title level={4}>{section.title}</Title>
      {section.items.length ? <div className="outline-attachment-list">{section.items.map((item, index) => <div key={index}>{item.name}</div>)}</div> : <Text type="secondary">暂无历史附件</Text>}
    </article>
  );
  return (
    <article className="outline-section-card">
      <Title level={4}>{section.title}</Title>
      <div className={section.kind === 'cards' ? 'outline-record-list' : 'outline-info-grid'}>
        {(section.items || []).map((item, index) => <div className="outline-record" key={index}>
          {item.map(field => <div className="outline-field" key={field.label}><div className="outline-field-label">{field.label}</div><TextValue value={field.value} /></div>)}
        </div>)}
      </div>
    </article>
  );
};

const parseTextbook = value => {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(item => typeof item === 'string' ? item : Object.values(item).filter(Boolean).join(' · '));
  return String(value).split(/\r?\n|\|\|/).map(item => item.trim()).filter(Boolean);
};

export default function CourseOutlineDrawer({ open, course, onClose }) {
  const code = course?.course_code || course?.code || '';
  const [overview, setOverview] = useState(null);
  const [overviewError, setOverviewError] = useState('');
  const [groups, setGroups] = useState({});
  const [states, setStates] = useState({});
  const [downloading, setDownloading] = useState(false);
  const [overviewRetry, setOverviewRetry] = useState(0);

  const loadGroup = useCallback(async (group, signal) => {
    setStates(previous => ({ ...previous, [group]: 'loading' }));
    try {
      const data = await getCourseOutlineSections(code, group, signal ? { signal } : {});
      setGroups(previous => ({ ...previous, [group]: data }));
      setStates(previous => ({ ...previous, [group]: 'done' }));
      return data;
    } catch (error) {
      if (error.code === 'ERR_CANCELED') return null;
      setStates(previous => ({ ...previous, [group]: 'error' }));
      return null;
    }
  }, [code]);

  useEffect(() => {
    if (!open || !code) return undefined;
    const controller = new AbortController();
    setOverview(null); setOverviewError(''); setGroups({}); setStates({});
    getCourseOutlineOverview(code, { signal: controller.signal })
      .then(data => {
        setOverview(data);
        return GROUPS.reduce((chain, group) => chain.then(() => controller.signal.aborted ? null : loadGroup(group.key, controller.signal)), Promise.resolve());
      })
      .catch(error => { if (error.code !== 'ERR_CANCELED') setOverviewError(error.response?.data?.detail || '大纲概览加载失败'); });
    return () => controller.abort();
  }, [open, code, loadGroup, overviewRetry]);

  const title = useMemo(() => overview?.course_name || course?.course_name || course?.name || '课程大纲', [overview, course]);
  const textbooks = useMemo(() => parseTextbook(overview?.textbooks), [overview]);
  const download = async () => {
    if (!overview) return;
    setDownloading(true);
    try {
      const complete = { ...groups };
      for (const group of GROUPS) if (!complete[group.key]) complete[group.key] = await loadGroup(group.key);
      if (GROUPS.some(group => !complete[group.key])) throw new Error('incomplete');
      downloadCourseOutlineHtml({ overview, groups: complete });
    } catch (_error) { message.error('部分大纲内容尚未加载成功，请重试后下载'); }
    finally { setDownloading(false); }
  };

  return (
    <Drawer className="course-outline-drawer" width="min(980px, 96vw)" placement="right" open={open} onClose={onClose}
      title={<div className="outline-drawer-title"><Title level={4}>{title}</Title><Text type="secondary">{code}</Text></div>}
      extra={<Button icon={<DownloadOutlined />} loading={downloading} disabled={!overview} onClick={download}>下载大纲</Button>}>
      {overviewError ? <Alert type="error" showIcon message={overviewError} action={<Button size="small" onClick={() => setOverviewRetry(value => value + 1)}>重试</Button>} /> : !overview ? <Skeleton active paragraph={{ rows: 8 }} /> : (
        <div className="outline-layout">
          <nav className="outline-toc" aria-label="大纲目录">
            <a href="#outline-overview">课程概览</a>{GROUPS.map(group => <a key={group.key} href={`#outline-${group.key}`}>{group.label}</a>)}
          </nav>
          <div className="outline-content">
            <section id="outline-overview">
              <div className="outline-section-heading"><div><Text className="outline-eyebrow">OVERVIEW</Text><Title level={3}>课程概览</Title></div>
                <div className="outline-core-tags"><Tag color="blue">{overview.assessment_method || '考核方式待定'}</Tag><Tag color="geekblue">{overview.grading_scale || '成绩分制待定'}</Tag>{overview.course_nature && <Tag>{overview.course_nature}</Tag>}</div>
              </div>
              <div className="outline-fact-grid">
                <div><span>开课单位</span><strong>{overview.department || '待定'}</strong></div>
                <div><span>学分</span><strong>{overview.credits ?? '—'}</strong></div>
                <div><span>学时</span><strong>{overview.hours ?? '—'}</strong></div>
                <div><span>适用专业</span><strong>{overview.applicable_majors || '未注明'}</strong></div>
              </div>
              <article className="outline-prose-card"><Title level={4}>课程简介</Title><TextValue value={overview.introduction} /></article>
              <article className="outline-prose-card"><Title level={4}>教材</Title>{textbooks.length ? <div className="outline-textbook-list">{textbooks.map((item, index) => <div key={index}>{item}</div>)}</div> : <Text type="secondary">暂无教材信息</Text>}</article>
            </section>
            {GROUPS.map(group => <section id={`outline-${group.key}`} key={group.key}>
              <Title level={3}>{group.label}</Title>
              {states[group.key] === 'loading' && <div className="outline-loading"><Spin /><span>正在读取{group.label}…</span></div>}
              {states[group.key] === 'error' && <Alert type="warning" showIcon message={`${group.label}加载失败`} action={<Button icon={<ReloadOutlined />} size="small" onClick={() => loadGroup(group.key)}>重试</Button>} />}
              {states[group.key] === 'done' && ((groups[group.key]?.sections || []).length ? <div className="outline-section-list">{groups[group.key].sections.map((section, index) => <PresentedSection key={`${section.title}-${index}`} section={section} />)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无内容" />)}
            </section>)}
          </div>
        </div>
      )}
    </Drawer>
  );
}
