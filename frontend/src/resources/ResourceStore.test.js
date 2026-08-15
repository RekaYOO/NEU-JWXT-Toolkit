jest.mock('../services/api', () => ({
  getCacheEvents: jest.fn(),
  getCacheRefreshJob: jest.fn(),
  getCachedAcademicReport: jest.fn(),
  getCachedScores: jest.fn(),
  getFestivalActivitiesCache: jest.fn(),
  getOfflineAcademicReport: jest.fn(),
  getOfflineFestivalActivities: jest.fn(),
  getOfflineResearchTraining: jest.fn(),
  getOfflineScores: jest.fn(),
  getResearchTrainingCache: jest.fn(),
  requestCacheRefresh: jest.fn(),
}));

import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ResourceProvider, deriveCachedResourceLoading, useCachedResource,
} from './ResourceStore';
import {
  getFestivalActivitiesCache, requestCacheRefresh,
} from '../services/api';

const FestivalResourceProbe = () => {
  const resource = useCachedResource('festival-activities');
  return (
    <div data-testid="festival-resource-state">
      {resource.loading ? 'loading' : 'idle'}|{resource.data ? 'data' : 'no-data'}
    </div>
  );
};

describe('cached resource loading state', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('keeps a cache miss loading while its automatic refresh is active', () => {
    expect(deriveCachedResourceLoading({
      enabled: true,
      displayedData: null,
      state: { loading: false, syncState: 'running' },
    })).toBe(true);
  });

  test('does not cover available cache data with a loading state', () => {
    expect(deriveCachedResourceLoading({
      enabled: true,
      displayedData: { activities: [] },
      state: { loading: false, syncState: 'running' },
    })).toBe(false);
  });

  test('does not report loading before the user confirms cached mode', () => {
    expect(deriveCachedResourceLoading({
      enabled: false,
      displayedData: null,
      state: {},
    })).toBe(false);
  });

  test('treats an HTTP 200 available:false payload as a miss and starts refresh', async () => {
    getFestivalActivitiesCache.mockResolvedValue({
      available: false,
      activities: [],
      warnings: [],
    });
    requestCacheRefresh.mockReturnValue(new Promise(() => {}));
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <ResourceProvider>
          <FestivalResourceProbe />
        </ResourceProvider>,
      );
      await new Promise(resolve => setTimeout(resolve, 0));
    });

    expect(getFestivalActivitiesCache).toHaveBeenCalledTimes(1);
    expect(requestCacheRefresh).toHaveBeenCalledWith(
      'festival-activities',
      expect.objectContaining({ reason: 'page_swr' }),
    );
    expect(container.textContent).toBe('loading|no-data');

    await act(async () => root.unmount());
    container.remove();
  });

  test('does not discard the first cache read when the provider mounts with an identity', async () => {
    let resolveCache;
    getFestivalActivitiesCache.mockReturnValue(new Promise(resolve => {
      resolveCache = resolve;
    }));
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <ResourceProvider identity="test-account">
          <FestivalResourceProbe />
        </ResourceProvider>,
      );
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    expect(container.textContent).toBe('loading|no-data');

    await act(async () => {
      resolveCache({
        activities: [{ id: 'cached' }],
        cache: { revision: 'r1', is_stale: false },
      });
      await new Promise(resolve => setTimeout(resolve, 0));
    });

    expect(container.textContent).toBe('idle|data');
    expect(getFestivalActivitiesCache).toHaveBeenCalledTimes(1);

    await act(async () => root.unmount());
    container.remove();
  });
});
