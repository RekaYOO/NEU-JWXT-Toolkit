import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ResearchTrainingPage from './ResearchTrainingPage';
import { Modal } from 'antd';
import { cancelResearchEnrollment, verifyResearchOperation, getResearchTopic, setResearchTopicFavorite } from '../services/api';

jest.mock('../services/api', () => ({
  ...jest.requireActual('../services/api'),
  cancelResearchEnrollment: jest.fn(),
  verifyResearchOperation: jest.fn(),
  getResearchTopic: jest.fn(),
  setResearchTopicFavorite: jest.fn(),
}));

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
    applyAvailable: jest.fn(), refresh: jest.fn(), reloadAndApply: jest.fn().mockResolvedValue(mockSnapshot),
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

test('late topic detail does not replace the latest selection', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  const previousTopics = mockSnapshot.topics;
  mockSnapshot.topics = [{ topic_id: 'first', title: '甲' }, { topic_id: 'second', title: '乙' }];
  let finish;
  getResearchTopic.mockImplementation(id => id === 'first' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ title: '乙详情' }));
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<ResearchTrainingPage />));
    const buttons = [...container.querySelectorAll('button')].filter(button => button.textContent === '查看详情');
    await act(async () => buttons[0].click());
    await act(async () => buttons[1].click());
    await act(async () => finish({ title: '甲详情' }));
    expect(document.querySelector('.ant-drawer-body').textContent).toContain('乙详情');
    expect(document.querySelector('.ant-drawer-body').textContent).not.toContain('甲详情');
  } finally {
    await act(async () => root.unmount());
    container.remove();
    mockSnapshot.topics = previousTopics;
    window.matchMedia = originalMatchMedia;
    global.IS_REACT_ACT_ENVIRONMENT = false;
  }
});

test('pending favorite blocks another write and unlocks after failure', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  const previousTopics = mockSnapshot.topics;
  mockSnapshot.topics = [{ topic_id: 'first', title: '甲' }, { topic_id: 'second', title: '乙' }];
  let fail;
  setResearchTopicFavorite.mockReturnValueOnce(new Promise((resolve, reject) => { fail = reject; }));
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<ResearchTrainingPage />));
    const buttons = container.querySelectorAll('button[aria-label="收藏课题"]');
    await act(async () => buttons[0].click());
    expect(buttons[1].disabled).toBe(true);
    await act(async () => buttons[1].click());
    expect(setResearchTopicFavorite).toHaveBeenCalledTimes(1);
    await act(async () => fail(new Error('offline')));
    expect(buttons[1].disabled).toBe(false);
  } finally {
    await act(async () => root.unmount());
    container.remove();
    mockSnapshot.topics = previousTopics;
    window.matchMedia = originalMatchMedia;
    global.IS_REACT_ACT_ENVIRONMENT = false;
  }
});

test('unknown cancellation blocks further submissions and verifies without replay', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  const previousTopics = mockSnapshot.topics;
  mockSnapshot.topics = [{ topic_id: 'topic', title: '材料课题', is_registered: true, can_cancel: true }];
  cancelResearchEnrollment.mockResolvedValue({ success: false, state: 'unknown', message: '取消结果待核验' });
  verifyResearchOperation.mockResolvedValue({ success: true, state: 'confirmed', message: '官方记录已确认' });
  let confirmation;
  const confirm = jest.spyOn(Modal, 'confirm').mockImplementation(options => { confirmation = options; return { destroy() {} }; });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<ResearchTrainingPage />));
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '取消报名').click());
    expect(cancelResearchEnrollment).not.toHaveBeenCalled();
    await act(async () => confirmation.onOk());
    expect(container.textContent).toContain('科研报名操作结果待核验');
    expect([...container.querySelectorAll('button')].find(button => button.textContent === '取消报名').disabled).toBe(true);
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '核验报名结果').click());
    expect(verifyResearchOperation).toHaveBeenCalledWith('topic');
    expect(cancelResearchEnrollment).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain('科研报名操作结果待核验');
  } finally {
    await act(async () => root.unmount());
    container.remove();
    confirm.mockRestore();
    mockSnapshot.topics = previousTopics;
    window.matchMedia = originalMatchMedia;
    global.IS_REACT_ACT_ENVIRONMENT = false;
  }
});
