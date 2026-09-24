import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ResearchTrainingPage from './ResearchTrainingPage';

const mockSnapshot = {
  available: true,
  batch: {
    name: '示例批次', max_topics: 2, rank_limit_percent: 30,
    allow_failed_courses: false,
  },
  eligibility: { available: true, major_rank: '25.93', gpa: '3.6' },
  topics: [], total: 1,
  confirmed_topics: [{
    record_id: 'record-1', topic_id: 'topic-1', title: '已确认课题',
    journal_count: 2,
  }],
};
jest.mock('../resources/ResourceStore', () => ({
  useCachedResource: () => ({
    data: mockSnapshot, loading: false, syncState: 'idle', syncError: null,
    updateAvailable: false, availableData: null,
    applyAvailable: jest.fn(), refresh: jest.fn(),
  }),
}));

test('rank stays outside the four desktop rule cards and confirmed topic can be viewed', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = () => ({
    matches: true, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {},
  });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<ResearchTrainingPage offlineMode />));
    expect(container.querySelectorAll('.research-rule-grid > .ant-col')).toHaveLength(4);
    expect(container.querySelector('.research-personal-rank').textContent).toContain('25.93%');
    const confirmed = [...container.querySelectorAll('.ant-tabs-tab')]
      .find(node => node.textContent.includes('已确认课题'));
    await act(async () => confirmed.click());
    expect(container.textContent).toContain('已确认课题');
    expect([...container.querySelectorAll('button')].some(node => node.textContent === '查看课题')).toBe(true);
  } finally {
    await act(async () => root.unmount());
    container.remove();
    window.matchMedia = originalMatchMedia;
    global.IS_REACT_ACT_ENVIRONMENT = false;
  }
});
