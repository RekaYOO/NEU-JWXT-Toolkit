import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import TimetablePage, { MobileTimetableNotices, TIMETABLE_LOGIN_ERROR_TEXT } from './TimetablePage';
import {
  getTimetableTerms, getPersonalTimetable, getTimetableContext,
  getTimetableSchedule, searchTimetableTargets, getTimetableBootstrap, syncTimetable,
  getRoomAvailability,
} from '../services/api';
import {
  readBrowserTimetableCache, writeBrowserTimetableCache, subscribeBrowserTimetableCache,
} from '../resources/BrowserTimetableStore';

jest.mock('../services/api', () => ({
  getTimetableTerms: jest.fn(),
  getPersonalTimetable: jest.fn(),
  getTimetableContext: jest.fn(),
  getTimetableSchedule: jest.fn(),
  searchTimetableTargets: jest.fn(),
  getTimetableBootstrap: jest.fn(),
  syncTimetable: jest.fn().mockResolvedValue({ jobs: [] }),
  getRoomAvailability: jest.fn(),
  getTimetableTargetFilterOptions: jest.fn(),
  checkScheduleConflicts: jest.fn(),
}));
let mockRecoveryMode = false;
const mockTimetableMemory = { data: null, publish: jest.fn() };
jest.mock('../resources/ResourceStore', () => {
  return {
    useResourceMemory: () => mockTimetableMemory,
    useResourceIdentity: () => 'query-fixture',
    useResourceOfflineMode: () => false,
    useResourceRecoveryMode: () => mockRecoveryMode,
  };
});
jest.mock('../resources/BrowserTimetableStore', () => ({
  readBrowserTimetableCache: jest.fn().mockResolvedValue({ terms: [], personal: [] }),
  writeBrowserTimetableCache: jest.fn().mockResolvedValue(undefined),
  subscribeBrowserTimetableCache: jest.fn(() => () => {}),
}));

const termCode = '2026-2027-1';
const weeks = [1, 2, 3].map(number => ({ number, name: `第${number}周`, current: number === 1 }));
const context = {
  campuses: [{ code: '00', name: '南湖校区' }], weeks,
  sections: [{ number: 1, name: '第1节' }, { number: 2, name: '第2节' }],
};
const course = {
  id: 'course', course_name: '查询课程', weekday: 7, start_section: 1, end_section: 2,
  weeks: [1, 2], teachers: ['测试教师'], location: '测试教室',
};
const personal = {
  term_code: termCode, ...context, sections_by_campus: { '00': context.sections },
  courses: [{ ...course, course_name: '个人课程' }], unscheduled: [], practices: [], is_fresh: true,
};
const empty = request => ({ ...request, courses: [], unscheduled: [], practices: [] });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

describe('query timetable request lifecycle', () => {
  let container;
  let root;
  let header;
  beforeEach(() => {
    jest.clearAllMocks();
    mockRecoveryMode = false;
    mockTimetableMemory.data = null;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = () => ({
      matches: false, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    });
    global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    Element.prototype.scrollIntoView = jest.fn();
    Element.prototype.scrollTo = jest.fn();
    window.scrollTo = jest.fn();
    localStorage.clear();
    header = document.createElement('div');
    header.id = 'workspace-header-center';
    document.body.appendChild(header);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    getTimetableTerms.mockResolvedValue({ terms: [{ code: termCode, name: '测试学期', current: true }], current: termCode });
    getPersonalTimetable.mockResolvedValue(personal);
    getTimetableBootstrap.mockResolvedValue({ terms: [], personal: [] });
    syncTimetable.mockResolvedValue({ jobs: [] });
    readBrowserTimetableCache.mockResolvedValue({ terms: [], personal: [] });
    writeBrowserTimetableCache.mockResolvedValue(undefined);
    subscribeBrowserTimetableCache.mockImplementation(() => () => {});
    getTimetableContext.mockResolvedValue(context);
    getRoomAvailability.mockResolvedValue({ items: [], scanned: 0, complete: true });
    getTimetableSchedule.mockImplementation(async request => empty(request));
    searchTimetableTargets.mockImplementation(async request => ({
      items: [{ id: `${request.mode}-fixture`, name: '测试查询对象', details: {} }], total: 1, page: 1,
    }));
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    jest.useRealTimers();
    container.remove();
    header.remove();
    window.history.replaceState(null, '', '/');
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });
  const flush = async () => {
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  };
  const click = async element => {
    expect(element).toBeTruthy();
    await act(async () => element.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await flush();
  };
  const mountQuery = async label => {
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    await click([...container.querySelectorAll('[role="tab"]')].find(tab => tab.textContent === label));
    await click(container.querySelector('.timetable-target-result-card'));
  };
  const week = number => container.querySelector(`[data-week="${number}"]`);

  test('keeps the current weekday fresh after midnight without overriding a manually selected day', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-16T08:00:00'));
    const datedWeeks = [1, 2, 3].map((number, index) => ({
      number, name: `第${number}周`, current: number === 2,
      start_date: `2026-09-${String(6 + index * 7).padStart(2, '0')}`,
      end_date: `2026-09-${String(12 + index * 7).padStart(2, '0')}`,
    }));
    const cached = { ...personal, weeks: datedWeeks, courses: [{ ...course, weeks: [2, 3] }] };
    mockTimetableMemory.data = {
      payload: cached, terms: [{ code: termCode, name: '测试学期', current: true }],
      currentTermCode: termCode, campusCode: '00', weekNumber: 3, viewMode: 'week',
    };
    getPersonalTimetable.mockResolvedValue(cached);

    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();

    const selectedDay = () => container.querySelector('.timetable-mobile-day-selector .ant-segmented-item-selected')?.textContent;
    const highlightedDay = () => container.querySelector('.timetable-desktop:not(.is-mobile-compact) .timetable-grid-header .is-today')?.textContent;
    expect(week(2)?.classList.contains('is-selected')).toBe(true);
    expect(selectedDay()).toContain('周三'); // No Wednesday course is needed to select today.
    expect(highlightedDay()).toContain('星期三');

    jest.setSystemTime(new Date('2026-09-17T08:00:00'));
    await act(async () => window.dispatchEvent(new Event('focus')));
    expect(selectedDay()).toContain('周四');
    expect(highlightedDay()).toContain('星期四');

    await click([...container.querySelectorAll('.timetable-mobile-day-selector .ant-segmented-item')]
      .find(item => item.textContent.includes('周一')));
    jest.setSystemTime(new Date('2026-09-18T08:00:00'));
    await act(async () => document.dispatchEvent(new Event('visibilitychange')));
    expect(selectedDay()).toContain('周一');
    expect(highlightedDay()).toContain('星期五');
  });

  test('moves the followed weekday and teaching week together at the Sunday boundary', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-19T08:00:00'));
    const datedWeeks = [2, 3].map((number, index) => ({
      number, name: `第${number}周`, current: number === 2,
      start_date: `2026-09-${13 + index * 7}`,
      end_date: `2026-09-${19 + index * 7}`,
    }));
    const cached = { ...personal, weeks: datedWeeks, courses: [{ ...course, weeks: [2, 3] }] };
    mockTimetableMemory.data = {
      payload: cached, terms: [{ code: termCode, name: '测试学期', current: true }],
      currentTermCode: termCode, campusCode: '00', weekNumber: 2, viewMode: 'week',
    };
    getPersonalTimetable.mockResolvedValue(cached);
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    expect(week(2)?.classList.contains('is-selected')).toBe(true);

    jest.setSystemTime(new Date('2026-09-20T08:00:00'));
    await act(async () => window.dispatchEvent(new Event('focus')));
    expect(week(3)?.classList.contains('is-selected')).toBe(true);
    expect(container.querySelector('.timetable-mobile-day-selector .ant-segmented-item-selected')?.textContent).toContain('周日');

    await click(week(2));
    jest.setSystemTime(new Date('2026-09-21T08:00:00'));
    await act(async () => document.dispatchEvent(new Event('visibilitychange')));
    expect(week(2)?.classList.contains('is-selected')).toBe(true);
  });

  test('clock ticks and midnight preserve a manually previewed week and its day without reloading', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-19T23:59:00'));
    const datedWeeks = [2, 3].map((number, index) => ({
      number, name: `第${number}周`, current: number === 2,
      start_date: `2026-09-${13 + index * 7}`,
      end_date: `2026-09-${19 + index * 7}`,
    }));
    const cached = {
      ...personal, weeks: datedWeeks,
      courses: [{ ...course, weekday: 6, weeks: [2, 3], course_name: '周六课程' }],
    };
    mockTimetableMemory.data = {
      payload: cached, terms: [{ code: termCode, name: '测试学期', current: true }],
      currentTermCode: termCode, campusCode: '00', weekNumber: 2, viewMode: 'week',
    };
    getPersonalTimetable.mockResolvedValue(cached);
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    await click(week(3));
    const grid = container.querySelector('.timetable-grid');
    const mobile = container.querySelector('.timetable-mobile');
    const selectedDay = () => container.querySelector('.timetable-mobile-day-selector .ant-segmented-item-selected')?.textContent;
    const personalRequests = getPersonalTimetable.mock.calls.length;
    const queryRequests = getTimetableSchedule.mock.calls.length;
    expect(week(3)?.classList.contains('is-selected')).toBe(true);
    expect(selectedDay()).toContain('周六');

    await act(async () => jest.advanceTimersByTime(30_000));
    expect(container.querySelector('.timetable-grid')).toBe(grid);
    expect(container.querySelector('.timetable-mobile')).toBe(mobile);
    expect(selectedDay()).toContain('周六');
    await act(async () => jest.advanceTimersByTime(90_000));
    expect(week(3)?.classList.contains('is-selected')).toBe(true);
    expect(selectedDay()).toContain('周六');
    expect(container.querySelector('.timetable-grid')).toBe(grid);
    expect(container.querySelector('.timetable-mobile')).toBe(mobile);
    expect(container.querySelector('.timetable-loading')).toBeNull();
    expect(getPersonalTimetable).toHaveBeenCalledTimes(personalRequests);
    expect(getTimetableSchedule).toHaveBeenCalledTimes(queryRequests);
  });

  test('clock ticks do not repeat a queried timetable request or replace its preview', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-19T23:59:00'));
    await mountQuery('教室课表');
    await click(week(2));
    const grid = container.querySelector('.timetable-grid');
    const requestCount = getTimetableSchedule.mock.calls.length;
    await act(async () => jest.advanceTimersByTime(120_000));
    expect(week(2)?.classList.contains('is-selected')).toBe(true);
    expect(container.querySelector('.timetable-grid')).toBe(grid);
    expect(container.querySelector('.timetable-loading')).toBeNull();
    expect(getTimetableSchedule).toHaveBeenCalledTimes(requestCount);
  });

  test('midnight does not clear an existing room availability scan when its default week changes', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-19T23:59:00'));
    const datedWeeks = [2, 3].map((number, index) => ({
      number, name: `第${number}周`,
      start_date: `2026-09-${13 + index * 7}`,
      end_date: `2026-09-${19 + index * 7}`,
    }));
    getTimetableContext.mockResolvedValue({ ...context, weeks: datedWeeks });
    getRoomAvailability.mockResolvedValue({
      items: [{ id: 'room-1', name: '空教室' }], scanned: 1,
      cursor: 1, candidate_total: 1, complete: true, scanned_room_ids: ['room-1'],
    });
    await mountQuery('教室课表');
    await click([...container.querySelectorAll('button')].find(button => button.textContent === '筛选'));
    const modal = document.querySelector('.timetable-target-filter-modal');
    expect(modal).toBeTruthy();
    await click([...modal.querySelectorAll('button')].find(button => button.textContent === '开始扫描'));
    expect(modal.textContent).toContain('已完成：共检查 1 间教室');
    expect(modal.textContent).toContain('空教室');
    const requests = getRoomAvailability.mock.calls.length;

    await act(async () => jest.advanceTimersByTime(120_000));
    expect(modal.textContent).toContain('已完成：共检查 1 间教室');
    expect(modal.textContent).toContain('空教室');
    expect(getRoomAvailability).toHaveBeenCalledTimes(requests);
  });

  test('returning from a scanned room retains candidates and continues from the saved cursor', async () => {
    jest.useFakeTimers();
    const rooms = Array.from({ length: 4 }, (_, index) => ({
      id: `room-${index + 1}`, name: `空教室 ${index + 1}`,
    }));
    getRoomAvailability.mockImplementation(async request => ({
      items: [rooms[request.cursor]],
      scanned: 1,
      cursor: request.cursor + 1,
      candidate_total: rooms.length,
      complete: request.cursor === rooms.length - 1,
      scanned_room_ids: [rooms[request.cursor].id],
    }));
    await mountQuery('教室课表');
    const pendingPreview = deferred();
    searchTimetableTargets.mockReturnValueOnce(pendingPreview.promise);
    await click([...container.querySelectorAll('button')].find(button => button.textContent === '筛选'));
    const modal = document.querySelector('.timetable-target-filter-modal');
    const searchInput = modal.querySelector('.timetable-filter-search input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(searchInput, '实验楼');
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(searchInput.value).toBe('实验楼');
    await act(async () => jest.advanceTimersByTime(250));
    await click([...modal.querySelectorAll('button')].find(button => button.textContent === '开始扫描'));
    expect(getRoomAvailability).toHaveBeenCalledTimes(3);
    expect(getRoomAvailability.mock.calls[0][0].keyword).toBe('实验楼');
    expect(modal.textContent).toContain('已找到 3 间');
    await act(async () => pendingPreview.resolve({
      items: [{ id: 'ordinary-room', name: '普通候选' }], total: 1, page: 1,
    }));
    expect(modal.querySelectorAll('.timetable-target-result-card')).toHaveLength(3);
    expect(modal.textContent).not.toContain('普通候选');
    await click([...modal.querySelectorAll('.timetable-target-result-card')]
      .find(button => button.textContent.includes('空教室 1')));
    expect(container.textContent).toContain('空教室 1');

    const ordinaryRequests = searchTimetableTargets.mock.calls.length;
    await click([...container.querySelectorAll('button')].find(button => button.textContent === '筛选'));
    await act(async () => jest.advanceTimersByTime(300));
    expect(searchTimetableTargets).toHaveBeenCalledTimes(ordinaryRequests);
    expect(modal.querySelectorAll('.timetable-target-result-card')).toHaveLength(3);
    expect(modal.textContent).toContain('空教室 2');
    expect(modal.textContent).toContain('已找到 3 间');
    await click([...modal.querySelectorAll('button')].find(button => button.textContent === '继续查找'));
    expect(getRoomAvailability).toHaveBeenCalledTimes(4);
    expect(getRoomAvailability.mock.calls[3][0]).toEqual(expect.objectContaining({
      cursor: 3,
      seen_room_ids: ['room-1', 'room-2', 'room-3'],
      keyword: '实验楼',
    }));
    expect(modal.querySelectorAll('.timetable-target-result-card')).toHaveLength(4);
    expect(modal.textContent).toContain('已完成：共检查 4 间教室');
    await click([...modal.querySelectorAll('.timetable-target-result-card')]
      .find(button => button.textContent.includes('空教室 4')));
    await click([...container.querySelectorAll('button')].find(button => button.textContent === '筛选'));
    expect(modal.querySelectorAll('.timetable-target-result-card')).toHaveLength(4);
    expect(getRoomAvailability).toHaveBeenCalledTimes(4);
  });

  test('corrects the weekday when the current term arrives after a cached timetable', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-16T08:00:00'));
    const termsRequest = deferred();
    const datedWeeks = [{
      number: 2, name: '第2周', current: false,
      start_date: '2026-09-13', end_date: '2026-09-19',
    }];
    const cached = { ...personal, weeks: datedWeeks, courses: [{ ...course, weeks: [2] }] };
    mockTimetableMemory.data = {
      payload: cached,
      terms: [
        { code: '2025-2026-2', name: '上一学期' },
        { code: termCode, name: '测试学期' },
      ],
      currentTermCode: '2025-2026-2', campusCode: '00', weekNumber: 2, viewMode: 'week',
    };
    getTimetableTerms.mockReturnValue(termsRequest.promise);
    getPersonalTimetable.mockResolvedValue(cached);
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    const selectedDay = () => container.querySelector('.timetable-mobile-day-selector .ant-segmented-item-selected')?.textContent;
    expect(container.textContent).toContain('查询课程');
    expect(selectedDay()).toContain('周日');

    await act(async () => termsRequest.resolve({
      terms: [{ code: termCode, name: '测试学期', current: true }], current: termCode,
    }));
    await flush();
    expect(week(2)?.classList.contains('is-selected')).toBe(true);
    expect(selectedDay()).toContain('周三');
  });

  test.each(['week', 'term'])(
    'desktop cached %s timetable never auto-scrolls while breakpoints initialize',
    async viewMode => {
      window.matchMedia = () => ({
        matches: true, addListener: jest.fn(), removeListener: jest.fn(),
        addEventListener: jest.fn(), removeEventListener: jest.fn(),
      });
      mockTimetableMemory.data = {
        payload: personal, terms: [{ code: termCode, name: '测试学期', current: true }],
        currentTermCode: termCode, campusCode: '00', weekNumber: 1, viewMode,
      };
      await act(async () => root.render(
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <TimetablePage />
        </MemoryRouter>,
      ));
      await flush();
      expect(container.textContent).toContain('个人课程');
      expect(container.querySelector('.timetable-desktop-controls')).not.toBeNull();
      expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
      expect(window.scrollTo).not.toHaveBeenCalled();
      expect(container.querySelector('.timetable-page').style.paddingBottom).toBe('');
    },
  );

  test('browser weekly view returns to the current week on every page mount', async () => {
    const currentWeeks = [1, 2, 3].map(number => ({
      number, name: `第${number}周`, current: number === 2,
    }));
    const cached = { ...personal, weeks: currentWeeks };
    readBrowserTimetableCache.mockResolvedValue({
      terms: [{ code: termCode, name: '测试学期', current: true }],
      current: termCode,
      personal: [cached],
      viewState: { termCode, campusCode: '00', weekNumber: 3, viewMode: 'week' },
    });

    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();

    expect(week(2)?.classList.contains('is-selected')).toBe(true);
    expect(week(3)?.classList.contains('is-selected')).toBe(false);
  });

  test('opening the weekly page ignores a saved next term and its week', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-16T08:00:00'));
    const datedWeeks = [1, 2, 3].map((number, index) => ({
      number, name: `第${number}周`, current: number === 1,
      start_date: `2026-09-${String(6 + index * 7).padStart(2, '0')}`,
      end_date: `2026-09-${12 + index * 7}`,
    }));
    const currentPayload = { ...personal, weeks: datedWeeks, courses: [{ ...course, weeks: [2] }] };
    const nextTerm = '2026-2027-2';
    readBrowserTimetableCache.mockResolvedValue({
      terms: [{ code: termCode, name: '当前学期', current: true }, { code: nextTerm, name: '下一学期' }],
      current: termCode,
      personal: [currentPayload, { ...personal, term_code: nextTerm }],
      viewState: { termCode: nextTerm, campusCode: '00', weekNumber: 3, viewMode: 'week' },
    });
    getTimetableTerms.mockResolvedValue({
      terms: [{ code: termCode, name: '当前学期', current: true }, { code: nextTerm, name: '下一学期' }],
      current: termCode,
    });
    getPersonalTimetable.mockResolvedValue(currentPayload);

    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    expect(container.textContent).toContain('当前学期');
    expect(week(2)?.classList.contains('is-selected')).toBe(true);
    expect(week(3)?.classList.contains('is-selected')).toBe(false);
  });

  test('offline recovery opens the current cached week even when memory held the next term', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-16T08:00:00'));
    mockRecoveryMode = true;
    const nextTerm = '2026-2027-2';
    const datedWeeks = [1, 2, 3].map((number, index) => ({
      number, name: `第${number}周`, current: number === 1,
      start_date: `2026-09-${String(6 + index * 7).padStart(2, '0')}`,
      end_date: `2026-09-${12 + index * 7}`,
    }));
    const currentPayload = { ...personal, weeks: datedWeeks, courses: [{ ...course, weeks: [2] }] };
    const terms = [{ code: termCode, name: '当前学期', current: true }, { code: nextTerm, name: '下一学期' }];
    mockTimetableMemory.data = {
      payload: { ...personal, term_code: nextTerm }, terms, currentTermCode: termCode,
      campusCode: '00', weekNumber: 3, viewMode: 'week',
    };
    readBrowserTimetableCache.mockResolvedValue({
      terms, current: termCode, personal: [currentPayload, mockTimetableMemory.data.payload],
      viewState: { termCode: nextTerm, campusCode: '00', weekNumber: 3, viewMode: 'term' },
    });
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    expect(week(2)?.classList.contains('is-selected')).toBe(true);
    expect(container.textContent).toContain('当前学期');
    expect(getTimetableTerms).not.toHaveBeenCalled();
  });

  test('desktop week selector changes weeks by wheel without scrolling the page', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-16T08:00:00'));
    window.matchMedia = () => ({
      matches: true, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    });
    mockTimetableMemory.data = {
      payload: personal, terms: [{ code: termCode, name: '测试学期', current: true }],
      currentTermCode: termCode, campusCode: '00', weekNumber: 1, viewMode: 'week',
    };
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    const field = [...container.querySelectorAll('.timetable-desktop-controls label')]
      .find(item => item.textContent.startsWith('教学周'));
    expect(field).toBeTruthy();
    const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 120 });
    await act(async () => field.dispatchEvent(event));
    expect(event.defaultPrevented).toBe(true);
    expect(field.querySelector('.ant-select-selection-item')?.textContent).toContain('第2周');
    expect(window.scrollTo).not.toHaveBeenCalled();

    await act(async () => jest.advanceTimersByTime(150));
    await act(async () => field.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true, cancelable: true, deltaY: 120,
    })));
    expect(field.querySelector('.ant-select-selection-item')?.textContent).toContain('第3周');
    const beyondLastWeek = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 120 });
    await act(async () => field.dispatchEvent(beyondLastWeek));
    expect(beyondLastWeek.defaultPrevented).toBe(false);
    expect(field.querySelector('.ant-select-selection-item')?.textContent).toContain('第3周');
  });

  test.each([
    ['week', false, 3, '周日'],
    ['term', false, 2, '周日'],
    ['week', true, 3, '周六'],
    ['term', true, 2, '周六'],
  ])('mobile summary navigates in %s view (compact=%s)', async (range, compact, expectedWeek, expectedDay) => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-19T20:00:00'));
    const datedWeeks = [2, 3].map((number, index) => ({
      number, name: `第${number}周`, current: number === 2,
      start_date: `2026-09-${13 + index * 7}`,
      end_date: `2026-09-${19 + index * 7}`,
    }));
    const cached = {
      ...personal, weeks: datedWeeks,
      courses: [{ ...course, weeks: [3], start_time: '08:30', end_time: '10:00' }],
    };
    mockTimetableMemory.data = {
      payload: cached, terms: [{ code: termCode, name: '测试学期', current: true }],
      currentTermCode: termCode, campusCode: '00', weekNumber: 2, viewMode: 'week',
    };
    getPersonalTimetable.mockResolvedValue(cached);
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    await click(header.querySelector('.timetable-mobile-summary-trigger'));
    if (range === 'term') {
      await click([...header.querySelectorAll('.timetable-mobile-summary-view .ant-segmented-item')]
        .find(item => item.textContent === '学期课表'));
    }
    if (compact) await click(header.querySelector('.timetable-mobile-summary-compact .ant-switch'));
    const detail = header.querySelector('.timetable-mobile-summary-course');
    expect(detail?.tagName).toBe('BUTTON');
    expect(detail.querySelector('.timetable-mobile-summary-course-arrow')).not.toBeNull();
    Element.prototype.scrollIntoView.mockClear();
    await click(detail);
    await act(async () => jest.advanceTimersByTime(40));
    if (range === 'term' && compact) {
      expect(Element.prototype.scrollIntoView).not.toHaveBeenCalledWith({
        block: 'start', inline: 'nearest', behavior: 'smooth',
      });
    } else {
      expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({
        block: 'start', inline: 'nearest', behavior: 'smooth',
      });
    }
    expect(header.querySelector('.timetable-mobile-summary').classList.contains('expanded')).toBe(false);
    if (range === 'week') expect(week(expectedWeek)?.classList.contains('is-selected')).toBe(true);
    else expect(container.querySelector('.timetable-mobile-week-timeline')).toBeNull();
    if (compact) {
      expect(container.querySelector('.is-mobile-compact .timetable-grid-header .is-today')?.textContent)
        .toContain(expectedDay);
    } else {
      expect(container.querySelector('.timetable-mobile-day-selector .ant-segmented-item-selected')?.textContent)
        .toContain(expectedDay);
    }
  });

  test('mobile summary advances to the next course without remounting the page', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-16T09:50:00'));
    const cached = {
      ...personal,
      weeks: [{ number: 2, name: '第2周', current: true, start_date: '2026-09-13', end_date: '2026-09-19' }],
      courses: [
        { ...course, id: 'morning', course_name: '上午课程', weekday: 3, weeks: [2], start_time: '09:00', end_time: '10:00' },
        { ...course, id: 'later', course_name: '随后课程', weekday: 3, weeks: [2], start_time: '10:30', end_time: '11:30' },
      ],
    };
    mockTimetableMemory.data = {
      payload: cached, terms: [{ code: termCode, name: '测试学期', current: true }],
      currentTermCode: termCode, campusCode: '00', weekNumber: 2, viewMode: 'week',
    };
    getPersonalTimetable.mockResolvedValue(cached);
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    await click(header.querySelector('.timetable-mobile-summary-trigger'));
    expect(header.querySelector('.timetable-mobile-summary-trigger').textContent).toContain('上午课程');

    jest.setSystemTime(new Date('2026-09-16T10:01:00'));
    await act(async () => window.dispatchEvent(new Event('focus')));
    expect(header.querySelector('.timetable-mobile-summary-trigger').textContent).toContain('随后课程');
    expect(header.querySelector('.timetable-mobile-summary-course').getAttribute('aria-label')).toContain('随后课程');
    await click(header.querySelector('.timetable-mobile-summary-course'));
    expect(week(2)?.classList.contains('is-selected')).toBe(true);
    expect(container.querySelector('.timetable-mobile-day-selector .ant-segmented-item-selected')?.textContent)
      .toContain('周三');
  });

  test.each(['教室课表', '教师课表', '班级课表'])(
    '%s summary shows course information without a navigation action', async label => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-13T08:00:00'));
    const queryCourse = {
      ...course, start_time: '09:00', end_time: '10:00', weeks: [1, 2],
    };
    getTimetableSchedule.mockImplementation(async request => ({
      ...empty(request), courses: [queryCourse],
    }));
    await mountQuery(label);
    await click(header.querySelector('.timetable-mobile-summary-trigger'));
    const detail = header.querySelector('.timetable-mobile-summary-course');
    expect(detail?.tagName).toBe('DIV');
    expect(detail.textContent).toContain('查询课程');
    expect(detail.querySelector('.timetable-mobile-summary-course-arrow')).toBeNull();
    Element.prototype.scrollIntoView.mockClear();
    await click(detail);
    expect(header.querySelector('.timetable-mobile-summary').classList.contains('expanded')).toBe(true);
    expect(week(1)?.classList.contains('is-selected')).toBe(true);
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  test.each(['week', 'term'])(
    'small-screen cached %s timetable retains its initial auto-focus',
    async viewMode => {
      mockTimetableMemory.data = {
        payload: personal, terms: [{ code: termCode, name: '测试学期', current: true }],
        currentTermCode: termCode, campusCode: '00', weekNumber: 1, viewMode,
      };
      await act(async () => root.render(
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <TimetablePage />
        </MemoryRouter>,
      ));
      await flush();
      expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({
        block: 'start', inline: 'nearest', behavior: 'auto',
      });
      const anchors = Element.prototype.scrollIntoView.mock.instances;
      expect(anchors.every(anchor => viewMode === 'week'
        ? anchor.querySelector('[data-week]') !== null
        : anchor.classList.contains('timetable-mobile-day-selector'))).toBe(true);
    },
  );

  test('keeps a cached personal timetable stable when online mode takes over', async () => {
    const termsRequest = deferred();
    const bootstrapRequest = deferred();
    const cached = {
      ...personal,
      source: 'browser',
      is_fresh: false,
      cache: { saved_at: '2026-09-09T08:00:00Z' },
    };
    mockRecoveryMode = true;
    readBrowserTimetableCache.mockResolvedValue({
      terms: [{ code: termCode, name: '测试学期', current: true }],
      current: termCode,
      personal: [cached],
      viewState: { termCode, campusCode: '00', weekNumber: 1, viewMode: 'week' },
    });
    getTimetableTerms.mockReturnValue(termsRequest.promise);
    getTimetableBootstrap.mockReturnValue(bootstrapRequest.promise);

    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    const timetable = container.querySelector('.timetable-desktop');
    expect(timetable).not.toBeNull();
    expect(container.textContent).toContain('个人课程');

    mockRecoveryMode = false;
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();

    expect(container.querySelector('.timetable-desktop')).toBe(timetable);
    expect(container.textContent).toContain('个人课程');
    expect(container.querySelector('.timetable-refresh-button.ant-btn-loading')).toBeNull();
    expect(getPersonalTimetable).not.toHaveBeenCalled();

    await act(async () => {
      termsRequest.resolve({
        terms: [{ code: termCode, name: '测试学期', current: true }],
        current: termCode,
      });
      bootstrapRequest.resolve({
        terms: [{ code: termCode, name: '测试学期', current: true }],
        current: termCode,
        personal: [{ ...cached, cache: { saved_at: '2026-09-09T08:05:00Z' } }],
      });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('.timetable-desktop')).toBe(timetable);
    expect(getPersonalTimetable).not.toHaveBeenCalled();
  });

  test.each(['教室课表', '教师课表', '班级课表'])(
    '%s places a service error only below the mobile results and retries there', async label => {
      const text = '教务系统课表服务暂时不可用（错误编号：test-error）';
      getTimetableSchedule.mockRejectedValueOnce({ response: { data: { detail: text } } });
      await mountQuery(label);
      const results = container.querySelector('.timetable-query-results');
      const notice = container.querySelector('.timetable-mobile-notices');
      expect(notice).not.toBeNull();
      expect(results.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(container.querySelectorAll('.timetable-error')).toHaveLength(1);
      expect(notice.textContent).toContain(text);
      await click([...notice.querySelectorAll('button')].find(button => button.textContent.replace(/\s/g, '') === '重试'));
      expect(container.querySelector('.timetable-mobile-notices')).toBeNull();
      expect(container.textContent).toContain('当前条件下暂无课程安排');
    },
  );

  test('keeps failed week changes in one bottom notice without repeating the error above old results', async () => {
    getTimetableSchedule.mockImplementation(async request => ({ ...empty(request), courses: [course] }));
    await mountQuery('教室课表');
    getTimetableSchedule.mockRejectedValueOnce({ response: { data: { detail: '课表服务暂时不可用' } } });
    await click(week(2));
    expect(container.querySelector('.timetable-query-transition').textContent)
      .toContain('并非当前选择范围');
    expect(container.querySelector('.timetable-query-transition').textContent).not.toContain('失败');
    expect(container.querySelectorAll('.timetable-mobile-notices [role="alert"]')).toHaveLength(1);
    expect(container.querySelector('.timetable-query-stale').hasAttribute('inert')).toBe(true);
  });

  test('places personal timetable startup errors below the mobile results', async () => {
    getTimetableTerms.mockRejectedValue({ response: { data: { detail: '学期服务暂时不可用' } } });
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TimetablePage />
      </MemoryRouter>,
    ));
    await flush();
    const results = container.querySelector('.timetable-query-results');
    const notice = container.querySelector('.timetable-mobile-notices');
    expect(notice.textContent).toContain('学期服务暂时不可用');
    expect(results.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelectorAll('.timetable-error')).toHaveLength(1);
  });

  test('preserves desktop errors above the results', async () => {
    window.matchMedia = () => ({
      matches: true, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    });
    getTimetableSchedule.mockRejectedValueOnce({ response: { data: { detail: '课表服务暂时不可用' } } });
    await mountQuery('教室课表');
    const results = container.querySelector('.timetable-query-results');
    const error = container.querySelector('.timetable-error');
    expect(error.compareDocumentPosition(results) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector('.timetable-mobile-notices')).toBeNull();
  });

  test('merges duplicate errors and recovery messages while retaining both actions', async () => {
    const onRetry = jest.fn();
    const onLogin = jest.fn();
    await act(async () => root.render(
      <MobileTimetableNotices
        error={{ message: '课表服务暂时不可用' }}
        conflictError="课表服务暂时不可用"
        cacheAuthFailure
        onRetry={onRetry}
        onLogin={onLogin}
      />,
    ));
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(container.textContent.split('课表服务暂时不可用')).toHaveLength(2);
    expect(container.textContent).toContain('登录已失效，请重新登录');
    await click([...container.querySelectorAll('button')].find(button => button.textContent.replace(/\s/g, '') === '重试'));
    await click([...container.querySelectorAll('button')].find(button => button.textContent === '重新登录'));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onLogin).toHaveBeenCalledTimes(1);
  });

  test('shows one login message and action when both request and cache report expired login', async () => {
    await act(async () => root.render(
      <MobileTimetableNotices
        error={{ message: TIMETABLE_LOGIN_ERROR_TEXT }}
        recoveryNotice="恢复中"
        cacheAuthFailure
      />,
    ));
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(container.textContent).toContain(TIMETABLE_LOGIN_ERROR_TEXT);
    expect(container.textContent).not.toContain('当前显示本机课表');
    expect(container.textContent).not.toContain('登录已失效，请重新登录');
    expect(container.querySelectorAll('button')).toHaveLength(1);
  });

  test.each(['教室课表', '教师课表', '班级课表'])(
    '%s distinguishes initial and consecutive empty weeks from pending requests', async label => {
      const first = deferred();
      getTimetableSchedule.mockImplementationOnce(() => first.promise);
      await mountQuery(label);
      expect(container.textContent).toContain('等待拉取课表');
      expect(container.textContent).not.toContain('当前条件下暂无课程安排');
      await act(async () => first.resolve(empty(getTimetableSchedule.mock.calls[0][0])));
      expect(container.textContent).toContain('当前条件下暂无课程安排');
      expect(container.textContent).not.toContain('等待拉取课表');
      const summary = header.querySelector('.timetable-mobile-summary');
      await click(week(2));
      expect(header.querySelector('.timetable-mobile-summary')).toBe(summary);
      expect(container.textContent).toContain('当前条件下暂无课程安排');
      expect(container.querySelector('[aria-busy="true"]')).toBeNull();
      await click(week(3));
      expect(container.textContent).toContain('当前条件下暂无课程安排');
      expect(getTimetableSchedule).toHaveBeenCalledTimes(3);
    },
  );

  test('keeps summary expansion and timetable nodes mounted while switching weeks', async () => {
    getTimetableSchedule.mockImplementation(async request => ({ ...empty(request), courses: [course] }));
    await mountQuery('教室课表');
    await click(header.querySelector('.timetable-mobile-summary-trigger'));
    const summary = header.querySelector('.timetable-mobile-summary');
    const grid = container.querySelector('.timetable-desktop');
    const request = deferred();
    getTimetableSchedule.mockImplementationOnce(() => request.promise);
    await click(week(2));
    expect(header.querySelector('.timetable-mobile-summary')).toBe(summary);
    expect(summary.classList.contains('expanded')).toBe(true);
    expect(container.querySelector('.timetable-desktop')).toBe(grid);
    expect(container.querySelector('.timetable-query-pending')).toBeNull();
    expect(container.querySelector('.timetable-query-transition').textContent)
      .toContain('正在从教务系统读取第 2 周教室课表');
    expect(container.querySelector('.timetable-query-transition .ant-skeleton')).toBeNull();
    expect(container.querySelector('.timetable-query-stale').hasAttribute('inert')).toBe(true);
    await act(async () => request.resolve(empty(getTimetableSchedule.mock.calls.at(-1)[0])));
    expect(summary.classList.contains('expanded')).toBe(true);
    expect(container.textContent).toContain('当前条件下暂无课程安排');
  });

  test.each([
    ['我的课表', false], ['我的课表', true],
    ['教室课表', false], ['教室课表', true],
    ['教师课表', false], ['教师课表', true],
    ['班级课表', false], ['班级课表', true],
    ['个人历史课表', false], ['个人历史课表', true],
  ])('%s switches view without remounting results (desktop=%s)', async (label, desktop) => {
    window.matchMedia = () => ({
      matches: desktop, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    });
    getTimetableSchedule.mockImplementation(async request => ({ ...empty(request), courses: [course] }));
    const historical = label === '个人历史课表';
    if (historical) window.history.replaceState(null, '', `/timetable?term=${termCode}&week=1`);
    if (historical) getTimetableTerms.mockResolvedValue({
      terms: [{ code: termCode, name: '历史学期' }, { code: '2027-2028-1', name: '当前学期', current: true }],
      current: '2027-2028-1',
    });
    if (label === '我的课表' || historical) {
      await act(async () => root.render(
        <MemoryRouter initialEntries={[`/timetable?term=${termCode}&week=1`]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <TimetablePage />
        </MemoryRouter>,
      ));
      await flush();
    } else await mountQuery(label);
    if (!desktop) await click(header.querySelector('.timetable-mobile-summary-trigger'));
    const controls = desktop ? container : header;
    const range = name => [...controls.querySelectorAll('.ant-segmented-item')]
      .find(item => item.textContent === (desktop ? (name === '学期课表' ? '全学期' : '按周') : name));
    const grid = container.querySelector('.timetable-desktop');
    const mobile = container.querySelector('.timetable-mobile');
    const summary = header.querySelector('.timetable-mobile-summary');
    const personalReads = getPersonalTimetable.mock.calls.length;
    const query = label !== '我的课表';
    expect(grid).not.toBeNull();
    for (const [name, termView] of [['学期课表', true], ['周课表', false]]) {
      const next = deferred();
      if (query) getTimetableSchedule.mockImplementationOnce(() => next.promise);
      await click(range(name));
      expect(container.querySelector('.timetable-desktop')).toBe(grid);
      expect(container.querySelector('.timetable-mobile')).toBe(mobile);
      expect(container.querySelector('.timetable-query-results .ant-skeleton')).toBeNull();
      if (query) {
        expect(mobile.classList.contains('is-term-view')).toBe(!termView);
        expect(container.querySelector('.timetable-query-transition').textContent)
          .toContain('暂显示上次查询结果');
        expect(container.querySelector('.timetable-query-stale').hasAttribute('inert')).toBe(true);
        await act(async () => next.resolve({
          ...empty(getTimetableSchedule.mock.calls.at(-1)[0]), courses: [course],
        }));
      }
      expect(container.querySelector('.timetable-desktop')).toBe(grid);
      expect(mobile.classList.contains('is-term-view')).toBe(termView);
      expect(container.querySelector('.timetable-query-transition')).toBeNull();
      if (!desktop) {
        expect(header.querySelector('.timetable-mobile-summary')).toBe(summary);
        expect(summary.classList.contains('expanded')).toBe(true);
      }
    }
    expect(getPersonalTimetable).toHaveBeenCalledTimes(personalReads);
    if (desktop) {
      expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
      expect(window.scrollTo).not.toHaveBeenCalled();
    }
  });

  test('does not let personal cache events overwrite a confirmed empty query', async () => {
    await mountQuery('教室课表');
    const bootstrapReads = getTimetableBootstrap.mock.calls.length;
    getTimetableBootstrap.mockResolvedValue({ current: termCode, personal: [personal] });
    await act(async () => window.dispatchEvent(new CustomEvent('neu-cache-event', {
      detail: { resource: 'personal-timetable' },
    })));
    await flush();
    expect(getTimetableBootstrap).toHaveBeenCalledTimes(bootstrapReads);
    expect(container.textContent).toContain('当前条件下暂无课程安排');
    expect(container.textContent).not.toContain('个人课程');
    expect(container.textContent).not.toContain('等待拉取课表');
  });

  test('ignores a late personal bootstrap response after selecting a query target', async () => {
    const bootstrap = deferred();
    getTimetableBootstrap.mockImplementationOnce(() => bootstrap.promise);
    await mountQuery('教室课表');
    await act(async () => bootstrap.resolve({ current: termCode, personal: [personal] }));
    expect(container.textContent).toContain('当前条件下暂无课程安排');
    expect(container.textContent).not.toContain('个人课程');
    expect(container.textContent).not.toContain('等待拉取课表');
  });

  test('stops loading on a mismatched response instead of treating it as pending or empty', async () => {
    getTimetableSchedule.mockImplementation(async request => ({ ...empty(request), week: 29 }));
    await mountQuery('教室课表');
    expect(container.textContent).toContain('返回的课表范围或数据格式不匹配');
    expect(container.textContent).not.toContain('等待拉取课表');
    expect(container.textContent).not.toContain('当前条件下暂无课程安排');
  });

  test('ends context loading when no teaching weeks were returned', async () => {
    getTimetableContext.mockResolvedValue({ ...context, weeks: [] });
    await mountQuery('教室课表');
    expect(container.textContent).toContain('该学期未返回教学周');
    expect(container.textContent).not.toContain('等待拉取课表');
    expect(getTimetableSchedule).not.toHaveBeenCalled();
  });

  test.each(['教室课表', '教师课表', '班级课表'])(
    '%s shows the original loading message until an empty campus response arrives', async label => {
      const conditions = deferred();
      getTimetableContext.mockImplementationOnce(() => conditions.promise);
      await mountQuery(label);
      expect(container.textContent).toContain(`正在从教务系统读取${label}`);
      expect(container.textContent).not.toContain('该学期暂无课程安排');
      expect(getTimetableSchedule).not.toHaveBeenCalled();
      await act(async () => conditions.resolve({
        ...context, campuses: [],
        weeks: weeks.map(item => ({ ...item, current: item.number === 2 })),
      }));
      expect(container.textContent).toContain('该学期暂无课程安排');
      expect(container.textContent).not.toContain('等待拉取课表');
      expect(container.querySelector('[aria-busy="true"]')).toBeNull();
      expect(container.querySelector('.timetable-error')).toBeNull();
      expect(getTimetableSchedule).not.toHaveBeenCalled();
    },
  );

  test.each(['教室课表', '教师课表', '班级课表'])(
    '%s keeps confirmed empty-campus results through week and range changes and can refresh', async label => {
      const secondWeekContext = {
        ...context, weeks: weeks.map(item => ({ ...item, current: item.number === 2 })),
      };
      getTimetableContext.mockResolvedValue({ ...secondWeekContext, campuses: [] });
      await mountQuery(label);
      const expectNoCampus = () => {
        expect(container.textContent).toContain('该学期暂无课程安排');
        expect(container.querySelector('.timetable-error')).toBeNull();
        expect(container.textContent).not.toContain('等待拉取课表');
        expect(container.textContent).not.toContain('当前条件下暂无课程安排');
        expect(container.querySelector('[aria-busy="true"]')).toBeNull();
        expect(getTimetableSchedule).not.toHaveBeenCalled();
      };
      expect(week(2).getAttribute('aria-selected')).toBe('true');
      expectNoCampus();
      await click(week(3));
      expect(week(3).getAttribute('aria-selected')).toBe('true');
      expectNoCampus();
      await click(week(2));
      expectNoCampus();
      await click(header.querySelector('.timetable-mobile-summary-trigger'));
      const range = label => [...header.querySelectorAll('.ant-segmented-item')]
        .find(item => item.textContent === label);
      await click(range('学期课表'));
      expectNoCampus();
      await click(range('周课表'));
      expectNoCampus();
      await click(container.querySelector('[aria-label="刷新课表"]'));
      expectNoCampus();
      getTimetableContext.mockResolvedValue(secondWeekContext);
      await click(container.querySelector('[aria-label="刷新课表"]'));
      expect(container.querySelector('.timetable-error')).toBeNull();
      expect(container.textContent).toContain('当前条件下暂无课程安排');
      expect(getTimetableSchedule).toHaveBeenCalledTimes(1);
      expect(getTimetableSchedule.mock.calls[0][0].week).toBe(2);
      await click(week(3));
      expect(container.textContent).toContain('当前条件下暂无课程安排');
      expect(getTimetableSchedule).toHaveBeenCalledTimes(2);
    },
  );

  test('a failed refresh of an empty context shows an error, not a new empty result', async () => {
    getTimetableContext.mockResolvedValueOnce({ ...context, campuses: [] });
    await mountQuery('教室课表');
    const refresh = deferred();
    getTimetableContext.mockImplementationOnce(() => refresh.promise);
    await click(container.querySelector('[aria-label="刷新课表"]'));
    expect(container.textContent).toContain('正在从教务系统读取教室课表');
    expect(container.textContent).not.toContain('该学期暂无课程安排');
    await act(async () => refresh.reject({ response: { data: { detail: '模拟网络错误' } } }));
    expect(container.textContent).toContain('模拟网络错误');
    expect(container.textContent).not.toContain('该学期暂无课程安排');
    expect(container.textContent).not.toContain('等待拉取课表');
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    expect(getTimetableSchedule).not.toHaveBeenCalled();
    await click(container.querySelector('.timetable-error button'));
    expect(container.textContent).toContain('当前条件下暂无课程安排');
  });

  test('a malformed campus response is not considered a confirmed empty term', async () => {
    getTimetableContext.mockResolvedValue({ ...context, campuses: null });
    await mountQuery('教室课表');
    expect(container.querySelector('.timetable-error')).not.toBeNull();
    expect(container.textContent).not.toContain('该学期暂无课程安排');
    expect(container.textContent).not.toContain('等待拉取课表');
    expect(getTimetableSchedule).not.toHaveBeenCalled();
  });

  test('stops loading when switching back from term view without teaching weeks', async () => {
    getTimetableContext.mockResolvedValue({ ...context, weeks: [] });
    await mountQuery('教室课表');
    await click(header.querySelector('.timetable-mobile-summary-trigger'));
    const range = label => [...header.querySelectorAll('.ant-segmented-item')]
      .find(item => item.textContent === label);
    await click(range('学期课表'));
    expect(container.textContent).toContain('当前条件下暂无课程安排');
    expect(getTimetableSchedule).toHaveBeenCalledTimes(1);
    await click(range('周课表'));
    expect(container.textContent).toContain('该学期未返回教学周');
    expect(container.textContent).not.toContain('正在读取');
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    expect(getTimetableSchedule).toHaveBeenCalledTimes(1);
    await click(range('学期课表'));
    expect(container.querySelector('.timetable-error')).toBeNull();
    expect(container.textContent).toContain('当前条件下暂无课程安排');
    expect(getTimetableSchedule).toHaveBeenCalledTimes(2);
  });

  test('a failed initial request can be retried to a confirmed empty result', async () => {
    getTimetableSchedule.mockRejectedValueOnce({ response: { data: { detail: '模拟网络错误' } } });
    await mountQuery('班级课表');
    expect(container.textContent).toContain('模拟网络错误');
    expect(container.textContent).not.toContain('等待拉取课表');
    await click(container.querySelector('.timetable-error button'));
    expect(container.textContent).toContain('当前条件下暂无课程安排');
    expect(container.querySelector('.timetable-error')).toBeNull();
  });

  test('keeps query controls on failure and ignores a superseded week response', async () => {
    await mountQuery('教师课表');
    const older = deferred();
    const newer = deferred();
    getTimetableSchedule.mockImplementationOnce(() => older.promise)
      .mockImplementationOnce(() => newer.promise);
    await click(week(2));
    const olderRequest = getTimetableSchedule.mock.calls.at(-1)[0];
    await click(week(3));
    await act(async () => newer.reject({ response: { data: { detail: '模拟网络错误' } } }));
    expect(container.textContent).toContain('模拟网络错误');
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    await act(async () => older.resolve({ ...empty(olderRequest), courses: [course] }));
    expect(week(3).getAttribute('aria-selected')).toBe('true');
    expect(container.textContent).toContain('模拟网络错误');
  });
});
