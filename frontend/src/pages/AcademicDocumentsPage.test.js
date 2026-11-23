import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import AcademicDocumentsPage from './AcademicDocumentsPage';
import { generateAcademicDocument, getAcademicDocuments } from '../services/api';
import { Modal } from 'antd';

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

  test('failed generation closes the preview and requires another confirmation', async () => {
    getAcademicDocuments.mockResolvedValue({ groups: [{ name: '成绩单', documents: [{ id: 'zh', name: '中文成绩单' }] }] });
    generateAcademicDocument.mockRejectedValueOnce(new Error('报告暂不可用'));
    const confirm = jest.spyOn(Modal, 'confirm').mockImplementation(() => ({ destroy() {} }));
    const preview = { document: document.implementation.createHTMLDocument('preview'), closed: false, close: jest.fn() };
    const open = jest.spyOn(window, 'open').mockReturnValue(preview);
    try {
      await act(async () => root.render(<MemoryRouter><AcademicDocumentsPage /></MemoryRouter>));
      const button = [...container.querySelectorAll('button')].find(element => element.textContent.includes('生成证明'));
      await act(async () => button.click());
      await act(async () => confirm.mock.calls[0][0].onOk());
      expect(preview.close).toHaveBeenCalledTimes(1);
      await act(async () => button.click());
      expect(confirm).toHaveBeenCalledTimes(2);
      await act(async () => confirm.mock.calls[1][0].onCancel());
      expect(generateAcademicDocument).toHaveBeenCalledTimes(1);
      expect(open).toHaveBeenCalledTimes(1);
    } finally {
      confirm.mockRestore();
      open.mockRestore();
    }
  });

  test('duplicate clicks share one confirmation and HTML preview has an isolated frame', async () => {
    getAcademicDocuments.mockResolvedValue({ groups: [{ name: '成绩单', documents: [{ id: 'zh', name: '中文成绩单', semester_limit: 50 }] }] });
    let confirmation;
    const confirm = jest.spyOn(Modal, 'confirm').mockImplementation(options => { confirmation = options; return { destroy() {} }; });
    const preview = { document: document.implementation.createHTMLDocument('preview'), closed: false, close: jest.fn() };
    const open = jest.spyOn(window, 'open').mockReturnValue(preview);
    const originalCreate = URL.createObjectURL;
    URL.createObjectURL = jest.fn().mockReturnValue('blob:synthetic-report');
    generateAcademicDocument.mockResolvedValue({ blob: new Blob(['<html>report</html>'], { type: 'text/html' }), filename: '中文成绩单.html', format: 'html' });
    try {
      await act(async () => root.render(<MemoryRouter><AcademicDocumentsPage /></MemoryRouter>));
      const button = [...container.querySelectorAll('button')].find(element => element.textContent.includes('生成证明'));
      await act(async () => { button.click(); button.click(); });
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(generateAcademicDocument).not.toHaveBeenCalled();
      await act(async () => Promise.all([confirmation.onOk(), confirmation.onOk()]));
      expect(generateAcademicDocument).toHaveBeenCalledTimes(1);
      const frame = preview.document.querySelector('iframe');
      expect(frame.getAttribute('sandbox')).toContain('allow-scripts');
      expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin');
      expect(preview.opener).toBeNull();
    } finally {
      confirm.mockRestore();
      open.mockRestore();
      URL.createObjectURL = originalCreate;
    }
  });
});
