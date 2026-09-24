import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Modal } from 'antd';
import SystemMessageNotice from './SystemMessageNotice';
import { clearSystemMessagePromptMemory } from '../utils/systemMessagePrompt';

jest.mock('antd', () => ({
  ...jest.requireActual('antd'),
  Modal: { ...jest.requireActual('antd').Modal, info: jest.fn() },
}));
jest.mock('../resources/ResourceStore', () => ({
  useResourceIdentity: () => mockIdentity,
  useCachedResource: () => ({
    updateData: jest.fn(),
    availableData: mockSystemMessagesData,
  }),
}));

const notice = {
  id: 'official-1', kind: 'reminder', title: '考试安排通知',
  content: '考场变更', sent_at: '2026-09-23 10:00:00', read: false,
};
let mockIdentity = 'test-account';
let mockSystemMessagesData = null;

describe('system message notice across page changes', () => {
  let container;
  let root;

  const render = async messages => {
    await act(async () => {
      root.render(<SystemMessageNotice kind="exam" messages={messages} />);
    });
  };

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    clearSystemMessagePromptMemory();
    mockIdentity = 'test-account';
    mockSystemMessagesData = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    Modal.info.mockImplementation(() => ({ destroy: jest.fn() }));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    Modal.info.mockReset();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('official read messages stay quiet and acknowledgement survives remount', async () => {
    await render([{ ...notice, read: true }]);
    expect(Modal.info).not.toHaveBeenCalled();
    await render([notice]);
    expect(Modal.info).toHaveBeenCalledTimes(1);
    await act(async () => Modal.info.mock.calls[0][0].onOk());
    await render([notice]);
    expect(Modal.info).toHaveBeenCalledTimes(1);
  });

  test('uses the shared resource snapshot after another hook acknowledges a message', async () => {
    mockSystemMessagesData = { messages: [{ ...notice, read: true }] };
    await render([notice]);
    expect(Modal.info).not.toHaveBeenCalled();
  });

  test('leaving before acknowledgement does not discard the unread notice', async () => {
    await render([notice]);
    await act(async () => root.unmount());
    root = createRoot(container);
    await render([notice]);
    expect(Modal.info).toHaveBeenCalledTimes(2);
  });

  test('switching accounts closes the previous account notice', async () => {
    await render([notice]);
    const previous = Modal.info.mock.results[0].value;
    mockIdentity = 'another-account';
    await render([notice]);
    expect(previous.destroy).toHaveBeenCalled();
    expect(Modal.info).toHaveBeenCalledTimes(2);
  });
});
