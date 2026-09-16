import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import TimetableDayAgenda from './TimetableDayAgenda';

const weeks = [2, 3].map((number, index) => ({ number, start_date: `2026-09-${13 + index * 7}`, end_date: `2026-09-${19 + index * 7}` }));
const first = { id: 'first', date: '2026-09-17', title: '讨论会议', location: '101', note: '携带资料', important: '重要', start_time: '08:00', end_time: '09:00' };
const other = { id: 'other', date: '2026-09-18', title: '其他日程', start_time: '09:00', end_time: '10:00' };
const moves = [{ source: '2026-09-20', target: '2026-09-17' }];

describe('saved timetable agenda management', () => {
  let container;
  let root;
  let saved;
  let onSave;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = () => ({ matches: false, addListener: jest.fn(), removeListener: jest.fn(), addEventListener: jest.fn(), removeEventListener: jest.fn() });
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
  const click = async element => {
    expect(element).toBeTruthy();
    await act(async () => element.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await flush();
  };
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
