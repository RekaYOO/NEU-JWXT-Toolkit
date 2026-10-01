import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, DatePicker, Empty, Form, Input, Popconfirm, Segmented, Select, Space, TimePicker, Tooltip } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, SaveOutlined, SwapOutlined, UndoOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { AdaptiveModal, useIsMobile } from './mobile/MobileUX';
import {
  agendaCoursesOnDate,
  agendaDateForDay,
  agendaDayContext,
  agendaEventOccursOnDate,
  injectTimetableAgenda,
} from '../utils/timetableAgenda';
import './TimetableDayAgenda.css';

const blank = { title: '', location: '', note: '', important: '', start_time: '08:00', end_time: '09:00' };
const AGENDA_DAYS = [7, 1, 2, 3, 4, 5, 6];
const AGENDA_DAY_NAMES = { 1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '日' };

const selectionRange = (values, start, end) => {
  const left = values.indexOf(start);
  const right = values.indexOf(end);
  if (left < 0 || right < 0) return [start];
  return values.slice(Math.min(left, right), Math.max(left, right) + 1);
};

function AgendaOccurrenceSelector({
  mode,
  weeks,
  weekday,
  selectedDates,
  selectedWeeks,
  onDatesChange,
  onWeeksChange,
  disabled,
}) {
  const isMobile = useIsMobile();
  const [rangeAnchor, setRangeAnchor] = useState(null);
  const rangeStartRef = useRef(null);
  const lastTapRef = useRef(null);
  const mouseDragRef = useRef(null);
  const ignoreClickRef = useRef(false);
  const scrollGestureRef = useRef(null);
  const orderedWeeks = [...(weeks || [])].sort((left, right) => Number(left.number) - Number(right.number));
  const dateCells = orderedWeeks.flatMap(week => AGENDA_DAYS.map(day => ({
    value: agendaDateForDay(weeks, week.number, day),
    week: Number(week.number),
    day,
  }))).filter(item => item.value);
  const dateValues = dateCells.map(item => item.value);
  const weekValues = orderedWeeks.map(item => Number(item.number));
  const values = mode === 'date' ? dateValues : weekValues;
  const currentSelection = mode === 'date' ? selectedDates : selectedWeeks;
  const valueKey = value => mode === 'date' ? String(value) : Number(value);
  useEffect(() => {
    setRangeAnchor(null);
    rangeStartRef.current = null;
    lastTapRef.current = null;
    mouseDragRef.current = null;
    ignoreClickRef.current = false;
    scrollGestureRef.current = null;
  }, [mode]);
  useEffect(() => {
    mouseDragRef.current = null;
    scrollGestureRef.current = null;
    ignoreClickRef.current = false;
    lastTapRef.current = null;
  }, [isMobile, disabled]);
  const setSelection = next => {
    if (mode === 'date') onDatesChange([...new Set(next.map(String))].sort());
    else onWeeksChange([...new Set(next.map(Number))].sort((a, b) => a - b));
  };
  const alternatingSelection = (selection, start) => {
    const anchor = values.indexOf(start ?? selection[0]);
    return selection.filter(value => Math.abs(values.indexOf(value) - anchor) % 2 === 0);
  };
  const selectValue = (value, event) => {
    if (ignoreClickRef.current) {
      ignoreClickRef.current = false;
      if (event.detail > 0) return;
    }
    const normalized = valueKey(value);
    const previous = lastTapRef.current;
    const now = Date.now();
    // Touch browsers may emit two ordinary clicks instead of a dblclick.
    // Preserve the first tap's selection so alternating weeks never sees a
    // transient single-cell selection.
    if (mode === 'week' && event.detail > 0 && previous?.eligible
      && previous.value === normalized
      && (event.detail === 2 || now - previous.at <= 360)) {
      setSelection(alternatingSelection(previous.selection, previous.start));
      setRangeAnchor(null);
      lastTapRef.current = null;
      return;
    }
    const alreadySelected = currentSelection.includes(normalized);
    lastTapRef.current = {
      value: normalized, at: now, selection: [...currentSelection],
      start: rangeStartRef.current,
      eligible: mode === 'week' && rangeAnchor == null && alreadySelected && currentSelection.length > 1,
    };
    if (rangeAnchor == null) {
      setRangeAnchor(normalized);
      if (!alreadySelected) setSelection([normalized]);
      return;
    }
    setSelection(selectionRange(values, rangeAnchor, normalized));
    rangeStartRef.current = rangeAnchor;
    setRangeAnchor(null);
  };
  const pointerDown = event => {
    if (disabled) return;
    const value = event.target.closest('[data-selection-value]')?.dataset.selectionValue;
    if (value == null) return;
    if (isMobile || event.pointerType !== 'mouse') {
      scrollGestureRef.current = { x: event.clientX, y: event.clientY };
      ignoreClickRef.current = false;
      return;
    }
    if (event.button !== 0) return;
    ignoreClickRef.current = false;
    mouseDragRef.current = { start: valueKey(value), x: event.clientX, y: event.clientY, moved: false };
  };
  const pointerMove = event => {
    if (disabled) return;
    const touch = scrollGestureRef.current;
    if (touch) {
      if (Math.hypot(event.clientX - touch.x, event.clientY - touch.y) > 8) {
        lastTapRef.current = null;
        ignoreClickRef.current = true;
      }
      return;
    }
    const drag = mouseDragRef.current;
    if (isMobile || !drag || (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 6)) return;
    const target = window.document.elementFromPoint?.(event.clientX, event.clientY)
      ?.closest('[data-selection-value]');
    if (!target || !event.currentTarget.contains(target)) return;
    // Capturing on press redirects ordinary button clicks to the grid.
    if (!drag.moved) event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.moved = true;
    setSelection(selectionRange(values, drag.start, valueKey(target.dataset.selectionValue)));
    rangeStartRef.current = drag.start;
    setRangeAnchor(null);
    lastTapRef.current = null;
  };
  const pointerEnd = () => {
    if (mouseDragRef.current?.moved) ignoreClickRef.current = true;
    mouseDragRef.current = null;
    scrollGestureRef.current = null;
  };
  const cancelGesture = () => {
    pointerEnd();
    lastTapRef.current = null;
    ignoreClickRef.current = true;
  };
  const gridEvents = {
    onPointerDown: pointerDown, onPointerMove: pointerMove,
    onPointerUp: pointerEnd, onPointerCancel: cancelGesture,
    onLostPointerCapture: pointerEnd,
    onPointerLeave: () => {
      if (mouseDragRef.current && !mouseDragRef.current.moved) mouseDragRef.current = null;
    },
    onScroll: () => { lastTapRef.current = null; },
  };
  const selected = new Set(mode === 'date' ? selectedDates : selectedWeeks.map(String));
  return (
    <section className="day-agenda-occurrence-selector" aria-label={mode === 'date' ? '选择日程日期' : '选择日程周次'}>
      {mode === 'date' ? (
        <div className="day-agenda-date-grid" {...gridEvents}>
          {orderedWeeks.map(week => (
            <div className="day-agenda-date-week" key={week.number}>
              <strong>第{week.number}周</strong>
              <div className="day-agenda-date-week-days">
                {AGENDA_DAYS.map(day => {
                  const value = agendaDateForDay(weeks, week.number, day);
                  if (!value) return <span key={`${week.number}-${day}`} aria-hidden="true" />;
                  return (
                    <button
                      type="button"
                      key={value}
                      className={[selected.has(value) && 'is-selected', rangeAnchor === value && 'is-range-anchor'].filter(Boolean).join(' ')}
                      aria-pressed={selected.has(value)}
                      disabled={disabled}
                      data-selection-value={value}
                      onClick={event => selectValue(value, event)}
                    >
                      <b>周{AGENDA_DAY_NAMES[day]}</b>
                      <small>{value.slice(5).replace('-', '/')}</small>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="day-agenda-week-grid" {...gridEvents}>
          {orderedWeeks.map(week => {
            const value = Number(week.number);
            return (
              <button
                type="button"
                key={value}
                className={[selected.has(String(value)) && 'is-selected', rangeAnchor === value && 'is-range-anchor'].filter(Boolean).join(' ')}
                aria-pressed={selected.has(String(value))}
                disabled={disabled}
                data-selection-value={value}
                onClick={event => selectValue(value, event)}
              >
                <b>第{value}周</b>
                <small>{week.start_date?.slice(5)}–{week.end_date?.slice(5)}</small>
              </button>
            );
          })}
        </div>
      )}
      <small className="day-agenda-selection-hint">
        {mode === 'date'
          ? `已选 ${selectedDates.length} 个日期；依次点击起点和终点可选择整段`
          : `每周${AGENDA_DAY_NAMES[weekday] || ''} · 已选 ${selectedWeeks.length} 周；点击起点和终点选整段，双击已选项隔周选择`}
      </small>
    </section>
  );
}

export default function TimetableDayAgenda({ date, weeks, courses, document, projection, writable, loading, error, onRetry, onSave, onClose, sections = [] }) {
  const [form] = Form.useForm();
  const [editing, setEditing] = useState(null);
  const [source, setSource] = useState(null);
  const [feedback, setFeedback] = useState('');
  const [saving, setSaving] = useState(false);
  const [activePicker, setActivePicker] = useState('');
  const [selectionMode, setSelectionMode] = useState('date');
  const [selectedDates, setSelectedDates] = useState([]);
  const [selectedWeeks, setSelectedWeeks] = useState([]);
  const [editorKey, setEditorKey] = useState(0);
  const savingRef = useRef(false);
  const editorRef = useRef(null);
  useEffect(() => { setEditing(null); setSource(null); setFeedback(''); setActivePicker(''); }, [date]);
  useEffect(() => {
    if (editing) editorRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [editing]);
  const pickerState = name => open => setActivePicker(previous => open ? name : previous === name ? '' : previous);
  if (!date) return null;
  const view = projection || injectTimetableAgenda({ courses, weeks, document, sections });
  const dateContext = agendaDayContext(weeks, date);
  const events = view.ended ? [] : (document.events || []).filter(event => agendaEventOccursOnDate(event, weeks, date));
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
    const mode = event?.selection_mode === 'week' || event?.mode === 'week' ? 'week' : 'date';
    const values = { ...blank, ...event, date: event?.date || date };
    const dates = event?.dates?.length ? event.dates : [event?.date || date];
    const eventWeeks = event?.weeks?.length ? event.weeks.map(Number) : [dateContext?.week].filter(Boolean);
    setFeedback('');
    setEditing(values);
    setEditorKey(previous => previous + 1);
    setSelectionMode(mode);
    setSelectedDates([...new Set(dates.map(value => String(value).slice(0, 10)))].sort());
    setSelectedWeeks([...new Set(eventWeeks)].sort((a, b) => a - b));
    form.resetFields();
    form.setFieldsValue({
      ...values,
      start: dayjs(`2000-01-01T${values.start_time}`),
      end: dayjs(`2000-01-01T${values.end_time}`),
    });
  };
  const quickTime = value => {
    const section = sections.find(item => String(item.number) === value);
    if (section?.start_time && section?.end_time) form.setFieldsValue({ start: dayjs(`2000-01-01T${section.start_time}`), end: dayjs(`2000-01-01T${section.end_time}`) });
  };
  const weekText = values => {
    const numbers = [...new Set((values || []).map(Number))].sort((a, b) => a - b);
    if (!numbers.length) return '';
    const ranges = [];
    let start = numbers[0];
    let end = numbers[0];
    numbers.slice(1).forEach(value => {
      if (value === end + 1) end = value;
      else {
        ranges.push(start === end ? `${start}` : `${start}–${end}`);
        start = value;
        end = value;
      }
    });
    ranges.push(start === end ? `${start}` : `${start}–${end}`);
    return ranges.join('、');
  };
  const items = [
    ...dayCourses.map((course, index) => ({ key: `course-${course.id}-${index}`, title: course.course_name, start_time: course.start_time, end_time: course.end_time,
      location: course.location, note: (course.teachers || []).join('、'), important: [course.course_nature || course.course_type, course.assessment_type].filter(Boolean).join(' · '),
      section: course.start_section ? `第${course.start_section}${course.end_section !== course.start_section ? `–${course.end_section}` : ''}节` : '时间待定', source: course.agenda_source })),
    ...events.map(event => ({
      ...event,
      key: event.id,
      event,
      scope: event.selection_mode === 'week'
        ? `每周${AGENDA_DAY_NAMES[event.weekday] || ''} · 第${weekText(event.weeks)}周`
        : (event.dates?.length || 0) > 1 ? `指定 ${event.dates.length} 个日期` : '',
    })),
  ].sort((a, b) => (a.start_time || '99:99').localeCompare(b.start_time || '99:99'));
  const weekday = ['日', '一', '二', '三', '四', '五', '六'][dayjs(date).day()];
  return <AdaptiveModal open title="当日日程" width={760} keyboard={!activePicker && !saving} onCancel={saving ? undefined : onClose} maskClosable={!saving} closable={!saving} footer={null} rootClassName="timetable-day-agenda-modal">
    <div className="day-agenda-heading"><div><strong>{dayjs(date).format('YYYY年M月D日')} · 星期{weekday}</strong>{dateContext && <small className="day-agenda-heading-meta">第{dateContext.week}周</small>}</div>
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
        {item.scope && <span className="day-agenda-scope">{item.scope}</span>}
        {item.location && <span className="day-agenda-location">{item.location}</span>}
        {item.note && <span className="day-agenda-note">{item.note}</span>}
        {item.important && <b className="day-agenda-important">{item.important}</b>}
        {item.source && <span className="day-agenda-note">调休自 {item.source}</span>}
      </article>)}
      {!items.length && !loading && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当天暂无日程" />}
    </div>
    {editing && writable && <Form form={form} layout="vertical" disabled={loading || saving} onFinish={values => {
      if (selectionMode === 'date' && !selectedDates.length) {
        setFeedback('请至少选择一个日期');
        return;
      }
      if (selectionMode === 'week' && (!selectedWeeks.length || !dateContext?.day)) {
        setFeedback('请至少选择一个教学周');
        return;
      }
      const event = { id: editing.id || `event-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        title: values.title.trim(), location: values.location || '', note: values.note || '', important: values.important || '',
        start_time: values.start.format('HH:mm'), end_time: values.end.format('HH:mm') };
      if (selectionMode === 'date') {
        event.date = selectedDates[0];
        if (selectedDates.length > 1) {
          event.selection_mode = 'date';
          event.dates = selectedDates;
        }
      } else {
        event.selection_mode = 'week';
        event.date = null;
        event.dates = [];
        event.weekday = editing.weekday || dateContext.day;
        event.weeks = selectedWeeks;
      }
      commit({ ...document, events: [...document.events.filter(item => item.id !== event.id), event] });
    }} className="day-agenda-form">
      <h3 className="day-agenda-editor-heading" ref={editorRef}>{editing.id ? '编辑日程' : '添加日程'}</h3>
      <Form.Item label="出现方式">
        <Segmented
          block
          value={selectionMode}
          options={[{ label: '选日期', value: 'date' }, { label: `按周${AGENDA_DAY_NAMES[editing.weekday || dateContext?.day] || ''}`, value: 'week' }]}
          onChange={value => {
            const next = String(value);
            setSelectionMode(next);
            if (next === 'date' && !selectedDates.length) setSelectedDates([date]);
            if (next === 'week' && !selectedWeeks.length && dateContext?.week) setSelectedWeeks([dateContext.week]);
          }}
        />
      </Form.Item>
      <AgendaOccurrenceSelector
        key={`${editorKey}-${selectionMode}`}
        mode={selectionMode}
        weeks={weeks}
        weekday={editing.weekday || dateContext?.day}
        selectedDates={selectedDates}
        selectedWeeks={selectedWeeks}
        onDatesChange={setSelectedDates}
        onWeeksChange={setSelectedWeeks}
        disabled={loading || saving}
      />
      <Form.Item name="title" label="标题" rules={[{ required: true, whitespace: true, message: '请输入标题' }]}><Input maxLength={120} /></Form.Item>
      <div className="day-agenda-times"><Form.Item name="start" label="开始时间" rules={[{ required: true, message: '请选择开始时间' }]}><TimePicker classNames={{ popup: { root: 'day-agenda-time-popup' } }} format="HH:mm" minuteStep={5} allowClear={false} inputReadOnly onOpenChange={pickerState('start')} /></Form.Item>
        <Form.Item name="end" label="结束时间" dependencies={['start']} rules={[{ required: true, message: '请选择结束时间' }, { validator: (_, value) => form.getFieldValue('start') && value && form.getFieldValue('start').format('HH:mm') < value.format('HH:mm') ? Promise.resolve() : Promise.reject(new Error('结束时间必须晚于开始时间')) }]}><TimePicker classNames={{ popup: { root: 'day-agenda-time-popup' } }} format="HH:mm" minuteStep={5} allowClear={false} inputReadOnly onOpenChange={pickerState('end')} /></Form.Item></div>
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
