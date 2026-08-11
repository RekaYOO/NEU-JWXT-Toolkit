import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import AcademicDocumentsPage from './AcademicDocumentsPage';
import { generateAcademicDocument, getAcademicDocuments } from '../services/api';

jest.mock('../services/api', () => ({
  generateAcademicDocument: jest.fn(),
  getAcademicDocuments: jest.fn(),
}));

const flush = () => new Promise(resolve => setTimeout(resolve, 0));

describe('AcademicDocumentsPage', () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('renders the dynamic official groups without generating any document', async () => {
    getAcademicDocuments.mockResolvedValue({
      groups: [
        {
          name: '成绩单',
          documents: [
            { id: 'zh', name: '中文成绩单打印', semester_limit: 50 },
            { id: 'average', name: '均分证明', semester_limit: 50 },
          ],
        },
        {
          name: '学籍证明',
          documents: [{ id: 'enrolment', name: '中文学籍证明', semester_limit: 50 }],
        },
      ],
    });

    await act(async () => {
      root.render(
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <AcademicDocumentsPage />
        </MemoryRouter>,
      );
      await flush();
    });

    expect(container.textContent).toContain('中文成绩单打印');
    expect(container.textContent).toContain('均分证明');
    expect(container.textContent).toContain('中文学籍证明');
    expect(container.textContent).toContain('每学期最多 50 次');
    expect(generateAcademicDocument).not.toHaveBeenCalled();
  });
});
