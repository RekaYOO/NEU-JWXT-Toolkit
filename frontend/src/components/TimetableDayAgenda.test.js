import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import TimetableDayAgenda from './TimetableDayAgenda';

const weeks = [2, 3].map((number, index) => ({ number, start_date: `2026-09-${13 + index * 7}`, end_date: `2026-09-${19 + index * 7}` }));
const fullWeeks = Array.from({ length: 12 }, (_, index) => ({
  number: index + 2,
  start_date: new Date(Date.UTC(2026, 8, 13 + index * 7)).toISOString().slice(0, 10),
  end_date: new Date(Date.UTC(2026, 8, 19 + index * 7)).toISOString().slice(0, 10),
}));
const first = { id: 'first', date: '2026-09-17', title: '讨论会议', location: '101', note: '携带资料', important: '重要', start_time: '08:00', end_time: '09:00' };
const other = { id: 'other', date: '2026-09-18', title: '其他日程', start_time: '09:00', end_time: '10:00' };
const moves = [{ source: '2026-09-20', target: '2026-09-17' }];

describe('saved timetable agenda management', () => {
  let container;
  let root;
  let saved;
  let onSave;
  let viewportWidth;
  let mediaQueries;
  const matchesViewport = query => {
    const bounds = [...query.matchAll(/(min|max)-width:\s*([\d.]+)px/g)];
    return bounds.length > 0 && bounds.every(([, bound, width]) => (
      bound === 'min' ? viewportWidth >= Number(width) : viewportWidth <= Number(width)
    ));
  };
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    viewportWidth = 1024;
    mediaQueries = [];
    window.matchMedia = query => {
      const listeners = new Set();
      const media = {
        media: query, matches: matchesViewport(query),
        addListener: listener => listeners.add(listener),
        removeListener: listener => listeners.delete(listener),
        addEventListener: (type, listener) => { if (type === 'change') listeners.add(listener); },
        removeEventListener: (type, listener) => { if (type === 'change') listeners.delete(listener); },
        listeners,
      };
      mediaQueries.push(media);
      return media;
    };
    global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    Element.prototype.scrollIntoView = jest.fn();
    Element.prototype.scrollTo = jest.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    onSave = jest.fn().mockResolvedValue(undefined);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });
  const mount = async (document = { revision: 4, events: [first, other], moves }, props = {}) => {
    saved = document;
    function Harness() {
      const [current, setCurrent] = useState(document);
      return <TimetableDayAgenda date="2026-09-17" weeks={weeks}
        courses={[{ id: 'official', course_name: '官方课程', weekday: 7, weeks: [3], start_section: 1, end_section: 2 }]}
        document={current} writable sections={[{ number: 3, start_time: '10:00', end_time: '11:00' }]}
        onClose={jest.fn()} onSave={async next => {
          await onSave(next);
          saved = { ...next, revision: next.revision + 1 };
          setCurrent(saved);
        }} {...props} />;
    }
    await act(async () => root.render(<ConfigProvider locale={zhCN}><Harness /></ConfigProvider>));
  };
  const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };
  const resize = async width => {
    await act(async () => {
      viewportWidth = width;
      mediaQueries.forEach(media => {
        const matches = matchesViewport(media.media);
        if (matches === media.matches) return;
        media.matches = matches;
        media.listeners.forEach(listener => listener({ matches, media: media.media }));
      });
    });
    await flush();
  };
  const click = async element => {
    expect(element).toBeTruthy();
    await act(async () => element.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await flush();
  };
  const clickWithDetail = async (element, detail) => {
    expect(element).toBeTruthy();
    await act(async () => element.dispatchEvent(new MouseEvent('click', { bubbles: true, detail })));
    await flush();
  };
  const pointer = async (target, type, pointerType, x = 0, y = 0) => {
    const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0, buttons: type === 'pointerup' ? 0 : 1 });
    Object.defineProperties(event, { pointerType: { value: pointerType }, pointerId: { value: 1 } });
    await act(async () => target.dispatchEvent(event));
    await flush();
  };
  const selectedValues = () => [...document.querySelectorAll('[data-selection-value][aria-pressed="true"]')]
    .map(item => item.dataset.selectionValue);
  const button = label => [...document.querySelectorAll('button')].find(item => item.textContent.replace(/\s/g, '') === label);
  const fill = async (id, value) => {
    const input = document.getElementById(id);
    const prototype = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => {
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  test('title opens a prefilled editor and saves changed fields without changing IDs or other data', async () => {
    await mount();
    await click(document.querySelector('[aria-label="修改日程：讨论会议"]'));
    expect(document.getElementById('title').value).toBe(first.title);
    expect(document.getElementById('note').value).toBe(first.note);
    expect(document.getElementById('start').value).toBe(first.start_time);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    await fill('title', '更新会议');
    await fill('location', '202');
    await fill('note', '新备注');
    await fill('important', '新重点');
    await act(async () => document.querySelector('.day-agenda-form .ant-select-selector').dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
    await flush();
    await click(document.querySelector('.ant-select-item-option'));
    await click(button('保存日程'));
    expect(saved.events).toEqual([other, { ...first, title: '更新会议', location: '202', note: '新备注', important: '新重点', start_time: '10:00', end_time: '11:00' }]);
    expect(saved.moves).toEqual(moves);
    expect(document.querySelector('[aria-label="编辑更新会议"]')).not.toBeNull();
    expect(document.querySelector('.day-agenda-form')).toBeNull();
    expect(first.title).toBe('讨论会议');
  });

  test('switching editors resets optional fields and cancel preserves saved events', async () => {
    const second = { ...other, date: first.date };
    await mount({ revision: 4, events: [first, second], moves });
    await click(document.querySelector('[aria-label="编辑讨论会议"]'));
    await fill('title', '未保存标题');
    await click(document.querySelector('[aria-label="编辑其他日程"]'));
    expect(document.getElementById('title').value).toBe(second.title);
    expect(document.getElementById('note').value).toBe('');
    expect(document.getElementById('important').value).toBe('');
    expect(document.getElementById('location').value).toBe('');
    await click(button('取消'));
    expect(onSave).not.toHaveBeenCalled();
    expect(saved.events).toEqual([first, second]);
    expect(document.querySelector('.day-agenda-form')).toBeNull();
  });

  test('confirmed deletion removes only the chosen manual event', async () => {
    await mount();
    await click(document.querySelector('[aria-label="删除讨论会议"]'));
    expect(onSave).not.toHaveBeenCalled();
    await click(button('确定'));
    expect(saved.events).toEqual([other]);
    expect(saved.moves).toEqual(moves);
    expect(document.querySelector('[aria-label="编辑讨论会议"]')).toBeNull();
    expect(document.querySelector('.day-agenda-list').textContent).toContain('官方课程');
    expect(document.querySelector('[aria-label="删除官方课程"]')).toBeNull();
  });

  test('failed edits keep the original event and allow retrying the draft', async () => {
    await mount();
    onSave.mockRejectedValueOnce(new Error('网络中断'));
    await click(document.querySelector('[aria-label="编辑讨论会议"]'));
    await fill('title', '待重试会议');
    await click(button('保存日程'));
    expect(saved.events).toEqual([first, other]);
    expect(document.getElementById('title').value).toBe('待重试会议');
    expect(document.querySelector('.timetable-day-agenda-modal').textContent).toContain('网络中断');
    await click(button('保存日程'));
    expect(onSave).toHaveBeenCalledTimes(2);
    expect(saved.events.filter(item => item.id === first.id)).toEqual([{ ...first, title: '待重试会议' }]);
  });

  test('supports selecting multiple concrete dates for one managed event', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    const dates = [...document.querySelectorAll('[data-selection-value]')];
    const startDate = dates.find(item => item.dataset.selectionValue === '2026-09-17');
    const extraDate = dates.find(item => item.dataset.selectionValue === '2026-09-18');
    await click(startDate);
    await click(extraDate);
    await fill('title', '多日期日程');
    await click(button('保存日程'));
    expect(saved.events).toEqual([expect.objectContaining({
      title: '多日期日程',
      selection_mode: 'date',
      dates: ['2026-09-17', '2026-09-18'],
      date: '2026-09-17',
    })]);
  });

  test('selects a concrete date range with start and end clicks', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    const date = value => document.querySelector(`[data-selection-value="${value}"]`);
    await click(date('2026-09-17'));
    await click(date('2026-09-19'));
    await fill('title', '日期范围日程');
    await click(button('保存日程'));
    expect(saved.events).toEqual([expect.objectContaining({
      title: '日期范围日程',
      selection_mode: 'date',
      dates: ['2026-09-17', '2026-09-18', '2026-09-19'],
      date: '2026-09-17',
    })]);
  });

  test('supports selecting multiple teaching weeks for the opened weekday', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    await click(document.querySelector('[data-selection-value="2"]'));
    const weekThree = document.querySelector('[data-selection-value="3"]');
    await click(weekThree);
    await fill('title', '每周日程');
    await click(button('保存日程'));
    expect(saved.events).toEqual([expect.objectContaining({
      title: '每周日程',
      selection_mode: 'week',
      weekday: 4,
      weeks: [2, 3],
      date: null,
    })]);
  });

  test('selects a teaching week range with start and end clicks', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    const weekTwo = document.querySelector('[data-selection-value="2"]');
    const weekThree = document.querySelector('[data-selection-value="3"]');
    await click(weekTwo);
    await click(weekThree);
    await fill('title', '周次范围日程');
    await click(button('保存日程'));
    expect(saved.events).toEqual([expect.objectContaining({
      title: '周次范围日程',
      selection_mode: 'week',
      weekday: 4,
      weeks: [2, 3],
      date: null,
    })]);
  });

  test.each([
    ['date', 'mouse', 1024],
    ['week', 'mouse', 1024],
    ['date', 'mouse', 375],
    ['week', 'mouse', 375],
    ['date', 'touch', 375],
    ['week', 'touch', 375],
  ])('%s range clicks reach the buttons with %s input at %ipx', async (mode, input, width) => {
    viewportWidth = width;
    await mount({ revision: 4, events: [], moves: [] }, { weeks: fullWeeks });
    await click(button('添加日程'));
    if (mode === 'week') {
      await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    }
    const grid = document.querySelector(mode === 'date' ? '.day-agenda-date-grid' : '.day-agenda-week-grid');
    let captured = null;
    grid.setPointerCapture = jest.fn(() => { captured = grid; });
    const press = async value => {
      const target = document.querySelector(`[data-selection-value="${value}"]`);
      await pointer(target, 'pointerdown', input, 10, 10);
      // A real browser targets click at the capture owner on pointer release.
      const releaseTarget = captured || target;
      await pointer(releaseTarget, 'pointerup', input, 10, 10);
      captured = null;
      await clickWithDetail(releaseTarget, 1);
    };
    const start = mode === 'date' ? '2026-09-17' : '2';
    const end = mode === 'date' ? '2026-09-19' : '9';
    await press(start);
    expect(document.querySelector('.is-range-anchor')?.dataset.selectionValue).toBe(start);
    await press(end);
    expect(selectedValues()).toEqual(mode === 'date'
      ? ['2026-09-17', '2026-09-18', '2026-09-19']
      : ['2', '3', '4', '5', '6', '7', '8', '9']);
    expect(grid.setPointerCapture).not.toHaveBeenCalled();
    if (mode === 'week') {
      await press('6');
      await press('6');
      expect(selectedValues()).toEqual(['2', '4', '6', '8']);
    }
  });

  test('double-clicking a selected week switches the range to alternating weeks', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    const weekTwo = document.querySelector('[data-selection-value="2"]');
    const weekThree = document.querySelector('[data-selection-value="3"]');
    await click(weekTwo);
    await click(weekThree);
    await clickWithDetail(weekThree, 1);
    await clickWithDetail(weekThree, 2);
    await fill('title', '隔周日程');
    await click(button('保存日程'));
    expect(saved.events).toEqual([expect.objectContaining({
      title: '隔周日程',
      selection_mode: 'week',
      weekday: 4,
      weeks: [2],
      date: null,
    })]);
  });

  test.each(['2', '6', '9'])('double-tapping selected week %s keeps 2,4,6,8 from the 2-9 range', async value => {
    await mount({ revision: 4, events: [], moves: [] }, { weeks: fullWeeks });
    await click(button('添加日程'));
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    await click(document.querySelector('[data-selection-value="2"]'));
    await click(document.querySelector('[data-selection-value="9"]'));
    const target = document.querySelector(`[data-selection-value="${value}"]`);
    await clickWithDetail(target, 1);
    expect(selectedValues()).toEqual(['2', '3', '4', '5', '6', '7', '8', '9']);
    // Android/WebView double taps may both have detail=1.
    await clickWithDetail(target, 1);
    expect(selectedValues()).toEqual(['2', '4', '6', '8']);
    await fill('title', '隔周日程');
    await click(button('保存日程'));
    expect(saved.events[0].weeks).toEqual([2, 4, 6, 8]);
  });

  test('reverse range selection keeps the original starting week when alternating', async () => {
    await mount({ revision: 4, events: [], moves: [] }, { weeks: fullWeeks });
    await click(button('添加日程'));
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    await click(document.querySelector('[data-selection-value="9"]'));
    await click(document.querySelector('[data-selection-value="2"]'));
    const target = document.querySelector('[data-selection-value="6"]');
    await clickWithDetail(target, 1);
    await clickWithDetail(target, 2);
    expect(selectedValues()).toEqual(['3', '5', '7', '9']);
  });

  test('double taps outside the selection and date-mode double taps do not alternate', async () => {
    await mount({ revision: 4, events: [], moves: [] }, { weeks: fullWeeks });
    await click(button('添加日程'));
    await click(document.querySelector('[data-selection-value="2026-09-17"]'));
    await click(document.querySelector('[data-selection-value="2026-09-19"]'));
    const date = document.querySelector('[data-selection-value="2026-09-18"]');
    await clickWithDetail(date, 1);
    await clickWithDetail(date, 2);
    expect(selectedValues()).toEqual(['2026-09-18']);
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    await click(document.querySelector('[data-selection-value="2"]'));
    await click(document.querySelector('[data-selection-value="9"]'));
    const outside = document.querySelector('[data-selection-value="12"]');
    await clickWithDetail(outside, 1);
    await clickWithDetail(outside, 2);
    expect(selectedValues()).toEqual(['12']);
  });

  test('touch scrolling does not change selection or continue a previous double tap', async () => {
    await mount({ revision: 4, events: [], moves: [] }, { weeks: fullWeeks });
    await click(button('添加日程'));
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    await click(document.querySelector('[data-selection-value="2"]'));
    await click(document.querySelector('[data-selection-value="9"]'));
    const target = document.querySelector('[data-selection-value="6"]');
    await pointer(target, 'pointerdown', 'touch', 20, 100);
    await pointer(target, 'pointermove', 'touch', 20, 40);
    await pointer(target, 'pointercancel', 'touch', 20, 40);
    await clickWithDetail(target, 1);
    expect(selectedValues()).toEqual(['2', '3', '4', '5', '6', '7', '8', '9']);
    await pointer(target, 'pointerdown', 'touch', 20, 100);
    await pointer(target, 'pointerup', 'touch', 20, 100);
    await clickWithDetail(target, 1);
    await act(async () => document.querySelector('.day-agenda-week-grid').dispatchEvent(new Event('scroll')));
    await clickWithDetail(target, 1);
    expect(selectedValues()).toEqual(['6']);
  });

  test('desktop mouse dragging still selects a range and its release click is ignored', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    const start = document.querySelector('[data-selection-value="2026-09-17"]');
    const end = document.querySelector('[data-selection-value="2026-09-19"]');
    const oldHitTest = document.elementFromPoint;
    document.elementFromPoint = jest.fn(() => end);
    const grid = document.querySelector('.day-agenda-date-grid');
    grid.setPointerCapture = jest.fn();
    try {
      await pointer(start, 'pointerdown', 'mouse', 10, 10);
      expect(grid.setPointerCapture).not.toHaveBeenCalled();
      await pointer(end, 'pointermove', 'mouse', 80, 10);
      expect(grid.setPointerCapture).toHaveBeenCalledWith(1);
      await pointer(end, 'pointerup', 'mouse', 80, 10);
      await clickWithDetail(end, 1);
      expect(selectedValues()).toEqual(['2026-09-17', '2026-09-18', '2026-09-19']);
    } finally {
      document.elementFromPoint = oldHitTest;
    }
  });

  test.each(['date', 'week'])('%s mode disables mouse drag at mobile widths but keeps range clicks and double taps', async mode => {
    viewportWidth = 375;
    await mount({ revision: 4, events: [], moves: [] }, { weeks: fullWeeks });
    await click(button('添加日程'));
    if (mode === 'week') {
      await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    }
    const startValue = mode === 'date' ? '2026-09-17' : '2';
    const endValue = mode === 'date' ? '2026-09-19' : '9';
    const start = document.querySelector(`[data-selection-value="${startValue}"]`);
    const end = document.querySelector(`[data-selection-value="${endValue}"]`);
    const grid = start.closest('.day-agenda-date-grid, .day-agenda-week-grid');
    grid.setPointerCapture = jest.fn();
    const oldHitTest = document.elementFromPoint;
    document.elementFromPoint = jest.fn(() => end);
    try {
      for (const width of [320, 375, 430, 767]) {
        await resize(width);
        await pointer(start, 'pointerdown', 'mouse', 10, 10);
        await pointer(end, 'pointermove', 'mouse', 80, 10);
        await pointer(end, 'pointerup', 'mouse', 80, 10);
        await clickWithDetail(end, 1);
        expect(selectedValues()).toEqual([startValue]);
        expect(grid.setPointerCapture).not.toHaveBeenCalled();
        expect(document.elementFromPoint).not.toHaveBeenCalled();
      }
      for (const target of [start, end]) {
        await pointer(target, 'pointerdown', 'mouse', 10, 10);
        await pointer(target, 'pointerup', 'mouse', 10, 10);
        await clickWithDetail(target, 1);
      }
      expect(selectedValues()).toEqual(mode === 'date'
        ? ['2026-09-17', '2026-09-18', '2026-09-19']
        : ['2', '3', '4', '5', '6', '7', '8', '9']);
      if (mode === 'week') {
        const target = document.querySelector('[data-selection-value="6"]');
        await clickWithDetail(target, 1);
        await clickWithDetail(target, 2);
        expect(selectedValues()).toEqual(['2', '4', '6', '8']);
      }
    } finally {
      document.elementFromPoint = oldHitTest;
    }
  });

  test('resizing to mobile stops a pending drag without losing the click range or saved draft', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    await fill('title', '保留草稿');
    const start = document.querySelector('[data-selection-value="2026-09-17"]');
    const end = document.querySelector('[data-selection-value="2026-09-19"]');
    const grid = document.querySelector('.day-agenda-date-grid');
    grid.setPointerCapture = jest.fn();
    const oldHitTest = document.elementFromPoint;
    document.elementFromPoint = jest.fn(() => end);
    try {
      await click(start);
      await pointer(start, 'pointerdown', 'mouse', 10, 10);
      await resize(375);
      await pointer(end, 'pointermove', 'mouse', 80, 10);
      await pointer(end, 'pointerup', 'mouse', 80, 10);
      expect(selectedValues()).toEqual(['2026-09-17']);
      expect(grid.setPointerCapture).not.toHaveBeenCalled();
      expect(document.getElementById('title').value).toBe('保留草稿');
      await pointer(end, 'pointerdown', 'mouse', 80, 10);
      await pointer(end, 'pointerup', 'mouse', 80, 10);
      await clickWithDetail(end, 1);
      expect(selectedValues()).toEqual(['2026-09-17', '2026-09-18', '2026-09-19']);
      await resize(768);
      await pointer(start, 'pointerdown', 'mouse', 10, 10);
      await pointer(end, 'pointermove', 'mouse', 80, 10);
      expect(grid.setPointerCapture).toHaveBeenCalledWith(1);
      await pointer(end, 'pointerup', 'mouse', 80, 10);
    } finally {
      document.elementFromPoint = oldHitTest;
    }
  });

  test('leaving before a mouse drag starts clears the pressed state', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    const start = document.querySelector('[data-selection-value="2026-09-17"]');
    const end = document.querySelector('[data-selection-value="2026-09-19"]');
    const oldHitTest = document.elementFromPoint;
    document.elementFromPoint = jest.fn(() => end);
    try {
      await pointer(start, 'pointerdown', 'mouse', 10, 10);
      await act(async () => start.dispatchEvent(new MouseEvent('pointerout', { bubbles: true, relatedTarget: document.body })));
      await pointer(end, 'pointermove', 'mouse', 80, 10);
      expect(selectedValues()).toEqual(['2026-09-17']);
      await pointer(end, 'pointerdown', 'mouse', 80, 10);
      await pointer(end, 'pointerup', 'mouse', 80, 10);
      await clickWithDetail(end, 1);
      expect(selectedValues()).toEqual(['2026-09-19']);
    } finally {
      document.elementFromPoint = oldHitTest;
    }
  });

  test('keyboard activation is not swallowed after a mouse drag release targets the grid', async () => {
    await mount({ revision: 4, events: [], moves: [] });
    await click(button('添加日程'));
    const start = document.querySelector('[data-selection-value="2026-09-17"]');
    const end = document.querySelector('[data-selection-value="2026-09-19"]');
    const oldHitTest = document.elementFromPoint;
    document.elementFromPoint = jest.fn(() => end);
    try {
      await pointer(start, 'pointerdown', 'mouse', 10, 10);
      await pointer(end, 'pointermove', 'mouse', 80, 10);
      await pointer(end, 'pointerup', 'mouse', 80, 10);
      await clickWithDetail(document.querySelector('.day-agenda-date-grid'), 1);
      await clickWithDetail(end, 0);
      expect(document.querySelector('.is-range-anchor')?.dataset.selectionValue).toBe('2026-09-19');
      await clickWithDetail(start, 0);
      expect(selectedValues()).toEqual(['2026-09-17', '2026-09-18', '2026-09-19']);
    } finally {
      document.elementFromPoint = oldHitTest;
    }
  });

  test('switching modes or editors clears an unfinished range without changing the draft selection', async () => {
    const second = { ...other, date: first.date, dates: [first.date, other.date] };
    await mount({ revision: 4, events: [first, second], moves: [] });
    await click(document.querySelector('[aria-label="编辑讨论会议"]'));
    await click(document.querySelector('[data-selection-value="2026-09-19"]'));
    await click(document.querySelector('[aria-label="编辑其他日程"]'));
    expect(selectedValues()).toEqual(['2026-09-17', '2026-09-18']);
    expect(document.querySelector('.is-range-anchor')).toBeNull();
    await click(document.querySelector('[data-selection-value="2026-09-18"]'));
    await click([...document.querySelectorAll('.ant-segmented-item')].find(item => item.textContent.includes('按周四')));
    expect(document.querySelector('.is-range-anchor')).toBeNull();
    await click(document.querySelector('[data-selection-value="2"]'));
    await click(document.querySelector('[data-selection-value="3"]'));
    expect(selectedValues()).toEqual(['2', '3']);
  });

  test('readonly mode has no management controls and loading disables them', async () => {
    await mount(undefined, { writable: false });
    expect(document.querySelector('[aria-label="编辑讨论会议"]')).toBeNull();
    expect(document.querySelector('[aria-label="删除讨论会议"]')).toBeNull();
    await mount(undefined, { loading: true });
    expect(document.querySelector('[aria-label="编辑讨论会议"]').disabled).toBe(true);
    expect(document.querySelector('[aria-label="删除讨论会议"]').disabled).toBe(true);
    await click(document.querySelector('[aria-label="修改日程：讨论会议"]'));
    expect(document.querySelector('.day-agenda-form')).toBeNull();
    expect(onSave).not.toHaveBeenCalled();
  });
});
