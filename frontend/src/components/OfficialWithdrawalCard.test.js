import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import OfficialWithdrawalCard from './OfficialWithdrawalCard';
import { deselectOfficialCourse, getOfficialWithdrawalCourses } from '../services/api';

jest.mock('../services/api', () => ({
  getOfficialWithdrawalCourses: jest.fn(),
  deselectOfficialCourse: jest.fn(),
}));

const course = (wid, canWithdraw) => ({
  wid,
  course_name: wid === 'SELF' ? '匿名实验课程' : '其他课程',
  course_code: 'TEST-1',
  course_serial: 'SERIAL-1',
  course_nature: '选修',
  course_category: '通识',
  can_withdraw: canWithdraw,
  unavailable_reason: canWithdraw ? '' : '非自选课程不允许退课',
  penalty_label: '50%',
  penalty_weight: 3,
});

describe('OfficialWithdrawalCard', () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = jest.fn(() => ({
      matches: false, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    }));
    getOfficialWithdrawalCourses.mockResolvedValue({
      term_code: '2099-2100-1',
      courses: [course('SELF', true), course('OTHER', false)],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.restoreAllMocks();
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('shows a compact entry instead of duplicating the withdrawal workflow', async () => {
    await act(async () => {
      root.render(<MemoryRouter><OfficialWithdrawalCard /></MemoryRouter>);
      await Promise.resolve();
    });
    expect(container.textContent).toContain('2门官方记录');
    expect(container.textContent).toContain('进入退课工作台');
    expect(container.querySelector('.official-withdrawal-row')).toBeNull();
    expect(container.querySelector('.official-withdrawal-card__entry > .ant-btn')).not.toBeNull();
  });

  test('does not call the write API from the entry card', async () => {
    await act(async () => {
      root.render(<MemoryRouter><OfficialWithdrawalCard /></MemoryRouter>);
      await Promise.resolve();
    });
    expect(deselectOfficialCourse).not.toHaveBeenCalled();
  });
});
