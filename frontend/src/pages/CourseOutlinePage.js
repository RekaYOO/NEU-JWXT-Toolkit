import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Card, Col, Empty, Input, Pagination, Row, Select, Skeleton, Tag, Typography } from 'antd';
import { FilterOutlined, ReadOutlined, SearchOutlined } from '@ant-design/icons';
import CourseOutlineDrawer from '../components/CourseOutlineDrawer';
import { getCourseOutlineSearchSchema, searchCourseOutlines } from '../services/api';
import './CourseOutlinePage.css';

const { Text, Title } = Typography;
const EMPTY_FILTERS = { KKDWDM: '', KCCCDM: '', KCJBDM: '', XF: [], XS: [] };
const RANGE_OPTIONS = {
  XF: [{ label: '1 学分及以下', value: '0,1' }, { label: '1.5–2 学分', value: '1.5,2' }, { label: '2.5–3 学分', value: '2.5,3' }, { label: '3.5–4 学分', value: '3.5,4' }, { label: '4 学分以上', value: '4.01,999' }],
  XS: [{ label: '16 学时及以下', value: '0,16' }, { label: '17–32 学时', value: '17,32' }, { label: '33–48 学时', value: '33,48' }, { label: '49–64 学时', value: '49,64' }, { label: '64 学时以上', value: '65,9999' }],
};
const encodeRange = value => Array.isArray(value) && value.length ? value.join(',') : undefined;
const decodeRange = value => value ? value.split(',').map(Number) : [];

export default function CourseOutlinePage() {
  const [schema, setSchema] = useState([]);
  const [keyword, setKeyword] = useState('');
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [result, setResult] = useState({ items: [], total: 0, page: 1, page_size: 20 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
  const requestRef = useRef(null);

  useEffect(() => { getCourseOutlineSearchSchema().then(data => setSchema(data.fields || [])).catch(() => setSchema([])); }, []);
  useEffect(() => {
    const controller = new AbortController(); requestRef.current?.abort(); requestRef.current = controller;
    const timer = setTimeout(async () => {
      setLoading(true); setError('');
      try { const data = await searchCourseOutlines({ keyword, filters, page: result.page, page_size: result.page_size }, { signal: controller.signal }); setResult(previous => ({ ...previous, ...data })); }
      catch (requestError) { if (requestError.code !== 'ERR_CANCELED') setError(requestError.response?.data?.detail || '课程大纲列表加载失败'); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [keyword, filters, result.page, result.page_size]);

  const fieldMap = useMemo(() => Object.fromEntries(schema.map(field => [field.key, field])), [schema]);
  const activeFilters = Object.entries(filters).filter(([, value]) => Array.isArray(value) ? value.length : Boolean(value));
  const setFilter = (key, value) => { setFilters(previous => ({ ...previous, [key]: value })); setResult(previous => ({ ...previous, page: 1 })); };
  const rangeLabel = (key, value) => RANGE_OPTIONS[key].find(option => option.value === encodeRange(value))?.label || value.join('–');

  return <div className="course-outline-page">
    <div className="course-outline-hero"><div><Title level={2}>课程大纲</Title><Text type="secondary">按课程代码或名称查找，完整大纲仅实时读取，不保存在本地。</Text></div><Tag icon={<ReadOutlined />} color="blue">实时读取</Tag></div>
    <Card className="outline-search-card">
      <Input size="large" allowClear prefix={<SearchOutlined />} value={keyword} onChange={event => { setKeyword(event.target.value); setResult(previous => ({ ...previous, page: 1 })); }} placeholder="输入课程名称或课程代码" />
      <div className="outline-filter-caption"><FilterOutlined /><Text strong>进一步筛选</Text><Text type="secondary">用于从同名课程或大量结果中快速缩小范围</Text></div>
      <div className="outline-filter-grid">
        {['KKDWDM', 'KCCCDM', 'KCJBDM'].map(key => <div className="outline-filter-field" key={key}><label>{fieldMap[key]?.label || ({ KKDWDM: '开课单位', KCCCDM: '课程层次', KCJBDM: '课程级别' }[key])}</label><Select allowClear showSearch optionFilterProp="label" disabled={!fieldMap[key]?.enabled} value={filters[key] || undefined} onChange={value => setFilter(key, value || '')} placeholder={fieldMap[key]?.enabled ? '全部' : '正在加载…'} options={fieldMap[key]?.options || []} /></div>)}
        {['XF', 'XS'].map(key => <div className="outline-filter-field" key={key}><label>{fieldMap[key]?.label || (key === 'XF' ? '学分' : '学时')}</label><Select allowClear value={encodeRange(filters[key])} onChange={value => setFilter(key, decodeRange(value))} placeholder="不限" options={RANGE_OPTIONS[key]} /></div>)}
      </div>
      {activeFilters.length > 0 && <div className="outline-active-filters">{activeFilters.map(([key, value]) => <Tag key={key} closable onClose={() => setFilter(key, Array.isArray(value) ? [] : '')}>{fieldMap[key]?.label || key}：{Array.isArray(value) ? rangeLabel(key, value) : fieldMap[key]?.options?.find(option => option.value === value)?.label || value}</Tag>)}<Button type="link" size="small" onClick={() => { setFilters(EMPTY_FILTERS); setResult(previous => ({ ...previous, page: 1 })); }}>清空筛选</Button></div>}
    </Card>
    <div className="outline-result-heading"><Text strong>课程列表</Text><Text type="secondary">共 {result.total} 门</Text></div>
    {loading ? <Skeleton active paragraph={{ rows: 8 }} /> : error ? <Empty description={error}><Button onClick={() => setFilters(previous => ({ ...previous }))}>重试</Button></Empty> : <Row gutter={[14, 14]}>{result.items.map(item => <Col xs={24} md={12} xl={8} key={item.course_code}><Card hoverable className="outline-course-card" onClick={() => setSelected(item)}><div className="outline-course-code">{item.course_code}</div><Title level={4} ellipsis={{ rows: 2 }}>{item.course_name || '未命名课程'}</Title><Text type="secondary" ellipsis>{item.department || '开课单位待定'}</Text><div className="outline-course-meta"><Tag>{item.credits ?? '-'} 学分</Tag><Tag>{item.hours ?? '-'} 学时</Tag>{item.level && <Tag>{item.level}</Tag>}</div><Button type="link" className="outline-view-button">查看大纲</Button></Card></Col>)}</Row>}
    {!loading && result.total > 0 && <Pagination className="outline-pagination" current={result.page} pageSize={result.page_size} total={result.total} showSizeChanger pageSizeOptions={[10, 20, 50]} onChange={(page, pageSize) => setResult(previous => ({ ...previous, page, page_size: pageSize }))} />}
    <CourseOutlineDrawer open={Boolean(selected)} course={selected} onClose={() => setSelected(null)} />
  </div>;
}
