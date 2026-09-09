import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import TimetablePage, { MobileTimetableNotices, TIMETABLE_LOGIN_ERROR_TEXT } from './TimetablePage';
import {
  getTimetableTerms, getPersonalTimetable, getTimetableContext,
  getTimetableSchedule, searchTimetableTargets, getTimetableBootstrap, syncTimetable,
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
    getTimetableSchedule.mockImplementation(async request => empty(request));
    searchTimetableTargets.mockImplementation(async request => ({
      items: [{ id: `${request.mode}-fixture`, name: '测试查询对象', details: {} }], total: 1, page: 1,
    }));
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    header.remove();
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
    expect(container.querySelector('.timetable-query-transition .ant-skeleton')).not.toBeNull();
    expect(container.querySelector('.timetable-query-stale').hasAttribute('inert')).toBe(true);
    await act(async () => request.resolve(empty(getTimetableSchedule.mock.calls.at(-1)[0])));
    expect(summary.classList.contains('expanded')).toBe(true);
    expect(container.textContent).toContain('当前条件下暂无课程安排');
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
