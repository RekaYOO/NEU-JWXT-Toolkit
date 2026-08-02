import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { message } from 'antd';
import FestivalActivitiesPage from './FestivalActivitiesPage';
import { useCachedResource } from '../resources/ResourceStore';
import {
  deleteFestivalActivitiesCache, downloadFestivalCertificates, getFestivalActivities,
} from '../services/api';
import { currentAcademicYear } from '../export/festivalActivityUtils';

jest.mock('../resources/ResourceStore', () => ({
  useCachedResource: jest.fn(),
}));

jest.mock('../services/api', () => ({
  deleteFestivalActivitiesCache: jest.fn(),
  downloadFestivalCertificates: jest.fn(),
  getFestivalActivities: jest.fn(),
}));

const resource = {
  data: { activities: [], warnings: [], cache: { revision: 'cached' } },
  displayedRevision: 'cached',
  availableData: null,
  availableRevision: '',
  updateAvailable: false,
  metadata: { isStale: false },
  loading: false,
  error: null,
  syncState: 'idle',
  syncError: null,
  refresh: jest.fn(),
  applyAvailable: jest.fn(),
  reloadAndApply: jest.fn(),
  clear: jest.fn(),
};

const findButton = (text) => [...document.querySelectorAll('button')].find(
  button => button.textContent.includes(text),
);

const click = (element) => {
  element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
};

const renderPage = async () => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  const page = () => (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <FestivalActivitiesPage />
    </MemoryRouter>
  );
  await act(async () => {
    root.render(page());
  });
  return {
    container,
    rerender: async () => {
      await act(async () => root.render(page()));
    },
    unmount: async () => {
      await act(async () => root.unmount());
      container.remove();
    },
  };
};

const settle = async (action) => {
  await act(async () => {
    action();
    await new Promise(resolve => setTimeout(resolve, 0));
  });
};

describe('FestivalActivitiesPage data mode flow', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = window.matchMedia || (() => ({
      matches: false,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
    }));
    global.ResizeObserver = global.ResizeObserver || class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  });

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    Object.assign(resource, {
      data: { activities: [], warnings: [], cache: { revision: 'cached' } },
      displayedRevision: 'cached',
      availableData: null,
      availableRevision: '',
      updateAvailable: false,
      metadata: { isStale: false },
      loading: false,
      error: null,
      syncState: 'idle',
      syncError: null,
    });
    useCachedResource.mockImplementation(() => resource);
    jest.spyOn(message, 'success').mockImplementation(() => {});
    jest.spyOn(message, 'error').mockImplementation(() => {});
    jest.spyOn(message, 'info').mockImplementation(() => {});
    jest.spyOn(message, 'warning').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.restoreAllMocks();
    document.body.innerHTML = '';
  });

  test('shows the entry modal and performs no request before confirmation', async () => {
    const view = await renderPage();
    expect(document.body.textContent).toContain('选择四节活动时间与读取方式');
    const dateValues = [...document.querySelectorAll('.ant-picker-input input')]
      .map(input => input.value)
      .filter(Boolean);
    expect(dateValues.length).toBeGreaterThanOrEqual(2);
    expect(dateValues.every(value => /^\d{4}-\d{2}-\d{2}$/.test(value))).toBe(true);
    expect(document.body.textContent).toContain('学年（当前）');
    expect(getFestivalActivities).not.toHaveBeenCalled();
    expect(deleteFestivalActivitiesCache).not.toHaveBeenCalled();
    expect(useCachedResource.mock.calls.every(([, options]) => !options.enabled)).toBe(true);
    await view.unmount();
  });

  test('keeps the modal open and does not GET when on-demand cache deletion fails', async () => {
    localStorage.setItem('neu_festival_activity_data_mode', 'on-demand');
    deleteFestivalActivitiesCache.mockRejectedValue({
      response: { data: { detail: '缓存删除失败' } },
    });
    const view = await renderPage();
    await settle(() => click(findButton('确认并读取活动')));
    expect(deleteFestivalActivitiesCache).toHaveBeenCalledTimes(1);
    expect(getFestivalActivities).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('选择四节活动时间与读取方式');
    await view.unmount();
  });

  test('isolates automatic mode from direct GET and cache deletion', async () => {
    const view = await renderPage();
    await settle(() => click(findButton('确认并读取活动')));
    expect(deleteFestivalActivitiesCache).not.toHaveBeenCalled();
    expect(getFestivalActivities).not.toHaveBeenCalled();
    expect(useCachedResource.mock.calls.some(([, options]) => options.enabled)).toBe(true);
    await view.unmount();
  });

  test('shows a fixed loader for an automatic cache miss and then renders refreshed data', async () => {
    resource.data = null;
    resource.loading = true;
    const view = await renderPage();
    await settle(() => click(findButton('确认并读取活动')));
    const header = document.querySelector('.festival-page__header');
    const loader = document.querySelector('.festival-loading');
    expect(loader).toBeTruthy();
    expect(header.compareDocumentPosition(loader) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(document.querySelector('.festival-controls')).toBeNull();
    expect(document.querySelector('.festival-stats')).toBeNull();
    expect(document.body.textContent).toContain('正在汇总四个分区的活动详情');
    expect(getFestivalActivities).not.toHaveBeenCalled();

    resource.data = {
      activities: [{
        id: 'auto-result',
        name: '自动刷新后的活动',
        section: '科技节',
        start_time: currentAcademicYear().start.add(1, 'day').format('YYYY-MM-DD'),
      }],
      warnings: [],
    };
    resource.loading = false;
    await view.rerender();
    expect(document.querySelector('.festival-loading')).toBeNull();
    expect(document.body.textContent).toContain('自动刷新后的活动');
    await view.unmount();
  });

  test('keeps cached activities visible while an automatic refresh runs in background', async () => {
    resource.data = {
      activities: [{
        id: 'cached-result',
        name: '刷新期间仍显示的活动',
        section: '创意节',
        start_time: currentAcademicYear().start.add(3, 'day').format('YYYY-MM-DD'),
      }],
      warnings: [],
    };
    resource.syncState = 'running';
    const view = await renderPage();
    await settle(() => click(findButton('确认并读取活动')));
    expect(document.querySelector('.festival-loading')).toBeNull();
    expect(document.querySelector('.festival-mobile-controls')).toBeTruthy();
    expect(document.querySelector('.festival-stats')).toBeTruthy();
    expect(document.body.textContent).toContain('正在后台刷新四节活动');
    expect(document.body.textContent).toContain('刷新期间仍显示的活动');
    await view.unmount();
  });

  test.each([320, 375, 430])(
    'uses compact mobile filters with an expandable full editor at %ipx',
    async (width) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
      resource.data = { activities: [], warnings: [] };
      const view = await renderPage();
      await settle(() => click(findButton('确认并读取活动')));

      expect(document.querySelector('.festival-controls')).toBeNull();
      expect(document.querySelector('.festival-mobile-controls')).toBeTruthy();
      expect(findButton('筛选与查看范围')).toBeTruthy();
      expect(findButton('打包证书（0）')).toBeTruthy();
      expect(document.querySelector('.mobile-filter-chips')).toBeTruthy();

      await settle(() => click(findButton('筛选与查看范围')));
      const mobileSheet = document.querySelector('.mobile-sheet');
      expect(mobileSheet).toBeTruthy();
      expect(mobileSheet.textContent).toContain('学年快捷选择');
      expect(mobileSheet.textContent).toContain('日期范围（含首尾）');
      expect(mobileSheet.textContent).toContain('活动分区');
      expect(mobileSheet.textContent).toContain('搜索活动');
      expect(mobileSheet.textContent).toContain('查看范围');
      await view.unmount();
    },
  );

  test('shows a fixed loader during on-demand GET and renders data after it resolves', async () => {
    localStorage.setItem('neu_festival_activity_data_mode', 'on-demand');
    deleteFestivalActivitiesCache.mockResolvedValue({ success: true });
    let resolveGet;
    getFestivalActivities.mockReturnValue(new Promise((resolve) => { resolveGet = resolve; }));
    const view = await renderPage();
    await settle(() => click(findButton('确认并读取活动')));
    expect(document.querySelector('.festival-loading')).toBeTruthy();
    expect(getFestivalActivities).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveGet({
        activities: [{
          id: 'direct-result',
          name: '按需读取后的活动',
          section: '创业节',
          start_time: currentAcademicYear().start.add(2, 'day').format('YYYY-MM-DD'),
        }],
        warnings: [],
      });
      await Promise.resolve();
    });
    expect(document.querySelector('.festival-loading')).toBeNull();
    expect(document.body.textContent).toContain('按需读取后的活动');
    await view.unmount();
  });

  test('shows an error alert and empty state after on-demand GET fails', async () => {
    localStorage.setItem('neu_festival_activity_data_mode', 'on-demand');
    deleteFestivalActivitiesCache.mockResolvedValue({ success: true });
    getFestivalActivities.mockRejectedValue({ response: { data: { detail: '学校系统读取失败' } } });
    const view = await renderPage();
    await settle(() => click(findButton('确认并读取活动')));
    expect(document.body.textContent).toContain('四节活动获取失败');
    expect(document.body.textContent).toContain('学校系统读取失败');
    expect(document.querySelector('.ant-empty')).toBeTruthy();
    await view.unmount();
  });

  test('on-demand mode deletes cache, uses direct GET, and keeps zero-certificate export enabled', async () => {
    localStorage.setItem('neu_festival_activity_data_mode', 'on-demand');
    deleteFestivalActivitiesCache.mockResolvedValue({ success: true });
    getFestivalActivities.mockResolvedValue({ activities: [], warnings: [] });
    downloadFestivalCertificates.mockResolvedValue({
      blob: new Blob(['zip']), filename: '证书.zip', succeeded: 0, failed: 0,
    });
    const view = await renderPage();
    await settle(() => click(findButton('确认并读取活动')));
    expect(deleteFestivalActivitiesCache).toHaveBeenCalledTimes(1);
    expect(getFestivalActivities).toHaveBeenCalledTimes(1);
    expect(resource.refresh).not.toHaveBeenCalled();
    expect(useCachedResource.mock.calls.every(([, options]) => !options.enabled)).toBe(true);
    const archiveButton = findButton('打包证书（0）');
    expect(archiveButton).toBeTruthy();
    expect(archiveButton.disabled).toBe(false);
    await view.unmount();
  });
});
