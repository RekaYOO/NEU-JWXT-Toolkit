import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, DatePicker, Empty, Form, Input, Popconfirm, Select, Space, TimePicker, Tooltip } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, SaveOutlined, SwapOutlined, UndoOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { AdaptiveModal } from './mobile/MobileUX';
import { agendaCoursesOnDate, agendaDayContext, injectTimetableAgenda } from '../utils/timetableAgenda';
import './TimetableDayAgenda.css';

const blank = { title: '', location: '', note: '', important: '', start_time: '08:00', end_time: '09:00' };

export default function TimetableDayAgenda({ date, weeks, courses, document, projection, writable, loading, error, onRetry, onSave, onClose, sections = [] }) {
  const [form] = Form.useForm();
  const [editing, setEditing] = useState(null);
  const [source, setSource] = useState(null);
  const [feedback, setFeedback] = useState('');
  const [saving, setSaving] = useState(false);
  const [activePicker, setActivePicker] = useState('');
  const savingRef = useRef(false);
  const editorRef = useRef(null);
  useEffect(() => { setEditing(null); setSource(null); setFeedback(''); setActivePicker(''); }, [date]);
  useEffect(() => {
    if (editing) editorRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [editing]);
  const pickerState = name => open => setActivePicker(previous => open ? name : previous === name ? '' : previous);
  if (!date) return null;
  const view = projection || injectTimetableAgenda({ courses, weeks, document, sections });
  const events = view.ended ? [] : (document.events || []).filter(event => event.date === date);
  const dayCourses = agendaCoursesOnDate(view.courses, weeks, date);
  const moves = view.ended ? [] : (document.moves || []).filter(move => move.target === date);
  const sourceCourses = source ? agendaCoursesOnDate(courses, weeks, source.format('YYYY-MM-DD')) : [];
  const commit = async next => {
    if (savingRef.current || !writable || loading || view.ended) return;
    savingRef.current = true;
    setSaving(true);
    setFeedback('');
    try { await onSave(next); setEditing(null); setSource(null); }
    catch (exc) {
      const detail = exc.response?.data?.detail;
      setFeedback(typeof detail === 'string' ? detail : detail ? '保存失败，请检查标题、日期及时间范围' : exc.message || '保存失败，请重试');
    }
    finally { savingRef.current = false; setSaving(false); }
  };
  const openEditor = event => {
    if (!writable || loading || savingRef.current || view.ended) return;
    const values = { ...blank, ...event };
    setFeedback('');
    setEditing(values);
    form.resetFields();
    form.setFieldsValue({ ...values, start: dayjs(`2000-01-01T${values.start_time}`), end: dayjs(`2000-01-01T${values.end_time}`) });
  };
  const quickTime = value => {
    const section = sections.find(item => String(item.number) === value);
    if (section?.start_time && section?.end_time) form.setFieldsValue({ start: dayjs(`2000-01-01T${section.start_time}`), end: dayjs(`2000-01-01T${section.end_time}`) });
  };
  const items = [
    ...dayCourses.map((course, index) => ({ key: `course-${course.id}-${index}`, title: course.course_name, start_time: course.start_time, end_time: course.end_time,
      location: course.location, note: (course.teachers || []).join('、'), important: [course.course_nature || course.course_type, course.assessment_type].filter(Boolean).join(' · '),
      section: course.start_section ? `第${course.start_section}${course.end_section !== course.start_section ? `–${course.end_section}` : ''}节` : '时间待定', source: course.agenda_source })),
    ...events.map(event => ({ ...event, key: event.id, event })),
  ].sort((a, b) => (a.start_time || '99:99').localeCompare(b.start_time || '99:99'));
  const weekday = ['日', '一', '二', '三', '四', '五', '六'][dayjs(date).day()];
  return <AdaptiveModal open title="当日日程" width={760} keyboard={!activePicker && !saving} onCancel={saving ? undefined : onClose} maskClosable={!saving} closable={!saving} footer={null} rootClassName="timetable-day-agenda-modal">
    <div className="day-agenda-heading"><strong>{dayjs(date).format('YYYY年M月D日')} · 星期{weekday}</strong>
      {writable && <Button icon={<PlusOutlined />} onClick={() => openEditor(null)} disabled={loading || saving}>添加日程</Button>}
    </div>
    {(error || feedback) && <Alert type="error" showIcon message={error || feedback} action={error && <Button onClick={onRetry} disabled={saving}>重试</Button>} />}
    {view.ended && <Alert type="info" showIcon message="该学期已结束，仅显示官方课程" />}
    {loading && <div role="status">正在读取日程…</div>}
    <div className="day-agenda-list">
      {items.map(item => <article className="day-agenda-item" key={item.key}>
        <div className="day-agenda-item-heading">{item.event && writable
          ? <button type="button" className="day-agenda-event-title" aria-label={`修改日程：${item.title}`} disabled={loading || saving} onClick={() => openEditor(item.event)}>{item.title}</button>
          : <strong>{item.title}</strong>}{item.event && writable && <Space size={4}>
          <Tooltip title="编辑日程"><Button type="text" icon={<EditOutlined />} aria-label={`编辑${item.title}`} disabled={loading || saving} onClick={() => openEditor(item.event)} /></Tooltip>
          <Popconfirm title="删除这条日程？" description={`删除“${item.title}”，其他日程和课程不受影响。`} disabled={loading || saving} onConfirm={() => commit({ ...document, events: document.events.filter(event => event.id !== item.id) })}><Button type="text" danger icon={<DeleteOutlined />} title="删除日程" aria-label={`删除${item.title}`} disabled={loading || saving} /></Popconfirm>
        </Space>}</div>
        <span>{item.start_time ? `${item.start_time}–${item.end_time}` : item.section}</span>
        {item.location && <span className="day-agenda-location">{item.location}</span>}
        {item.note && <span className="day-agenda-note">{item.note}</span>}
        {item.important && <b className="day-agenda-important">{item.important}</b>}
        {item.source && <span className="day-agenda-note">调休自 {item.source}</span>}
      </article>)}
      {!items.length && !loading && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当天暂无日程" />}
    </div>
    {editing && writable && <Form form={form} layout="vertical" disabled={loading || saving} onFinish={values => {
      const event = { id: editing.id || `event-${Date.now()}-${Math.random().toString(36).slice(2)}`, date,
        title: values.title.trim(), location: values.location || '', note: values.note || '', important: values.important || '',
        start_time: values.start.format('HH:mm'), end_time: values.end.format('HH:mm') };
      commit({ ...document, events: [...document.events.filter(item => item.id !== event.id), event] });
    }} className="day-agenda-form">
      <h3 className="day-agenda-editor-heading" ref={editorRef}>{editing.id ? '编辑日程' : '添加日程'}</h3>
      <Form.Item name="title" label="标题" rules={[{ required: true, whitespace: true, message: '请输入标题' }]}><Input maxLength={120} /></Form.Item>
      <div className="day-agenda-times"><Form.Item name="start" label="开始时间" rules={[{ required: true, message: '请选择开始时间' }]}><TimePicker format="HH:mm" minuteStep={5} allowClear={false} inputReadOnly onOpenChange={pickerState('start')} /></Form.Item>
        <Form.Item name="end" label="结束时间" dependencies={['start']} rules={[{ required: true, message: '请选择结束时间' }, { validator: (_, value) => form.getFieldValue('start') && value && form.getFieldValue('start').format('HH:mm') < value.format('HH:mm') ? Promise.resolve() : Promise.reject(new Error('结束时间必须晚于开始时间')) }]}><TimePicker format="HH:mm" minuteStep={5} allowClear={false} inputReadOnly onOpenChange={pickerState('end')} /></Form.Item></div>
      <Form.Item label="节次快捷选择"><Select onChange={quickTime} placeholder="选择节次" options={sections.filter(item => item.start_time && item.end_time).map(item => ({ value: String(item.number), label: `第${item.number}节 · ${item.start_time}–${item.end_time}` }))} /></Form.Item>
      <div className="day-agenda-fields"><Form.Item name="location" label="地点"><Input maxLength={200} /></Form.Item><Form.Item name="note" label="备注"><Input.TextArea maxLength={1000} autoSize={{ minRows: 1, maxRows: 4 }} /></Form.Item></div>
      <Form.Item name="important" label="重要信息"><Input maxLength={200} /></Form.Item>
      <div className="day-agenda-actions"><Button onClick={() => setEditing(null)} disabled={saving}>取消</Button><Button type="primary" icon={<SaveOutlined />} htmlType="submit" loading={saving}>保存日程</Button></div>
    </Form>}
    {moves.map(move => <div className="day-agenda-move" key={move.source}><span>{move.source} → {move.target}</span>
      {writable && <Popconfirm title="移除当天复制的课程？原日期不受影响。" onConfirm={() => commit({ ...document, moves: document.moves.filter(item => item.target !== move.target) })}><Button icon={<UndoOutlined />} disabled={saving}>撤销调休</Button></Popconfirm>}
    </div>)}
    {writable && <section className="day-agenda-adjustment"><h3>调休</h3>
      <div className="day-agenda-move-controls"><DatePicker value={source} onChange={setSource} onOpenChange={pickerState('date')} classNames={{ popup: { root: 'day-agenda-date-popup' } }} placeholder="课程原日期" inputReadOnly disabled={saving || loading} disabledDate={value => value.format('YYYY-MM-DD') <= dayjs().format('YYYY-MM-DD') || value.format('YYYY-MM-DD') === date || !agendaDayContext(weeks, value.format('YYYY-MM-DD'))} />
        <Popconfirm title={`将 ${source?.format('YYYY-MM-DD')} 的 ${sourceCourses.length} 门课程复制到 ${date}？`} description="原日期不变，保留当天已有课程。可撤销。" onConfirm={() => commit({ ...document, moves: [...document.moves, { source: source.format('YYYY-MM-DD'), target: date }] })}>
          <Button icon={<SwapOutlined />} disabled={!source || !sourceCourses.length || moves.length > 0 || saving || loading} loading={saving}>复制课程</Button>
        </Popconfirm></div>
      {source && <span className="day-agenda-note">{sourceCourses.length ? `${sourceCourses.length} 门：${sourceCourses.map(course => course.course_name).join('、')}` : '该日期没有可调入的课程'}</span>}
    </section>}
  </AdaptiveModal>;
}
