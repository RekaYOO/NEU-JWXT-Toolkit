import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { AdaptiveModal } from './MobileUX';

describe('AdaptiveModal visible viewport', () => {
  let container;
  let root;
  let viewport;
  let previousViewport;
  let previousAct;

  beforeEach(() => {
    previousAct = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    previousViewport = Object.getOwnPropertyDescriptor(window, 'visualViewport');
    viewport = Object.assign(new EventTarget(), { height: 812, offsetTop: 0 });
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: viewport });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    if (previousViewport) Object.defineProperty(window, 'visualViewport', previousViewport);
    else delete window.visualViewport;
    global.IS_REACT_ACT_ENVIRONMENT = previousAct;
  });

  const renderModal = async (props = {}) => {
    await act(async () => root.render(
      <AdaptiveModal open title="长筛选" okText="应用筛选" {...props}>
        {Array.from({ length: 30 }, (_, i) => <label key={i}>字段 {i}<input /></label>)}
      </AdaptiveModal>,
    ));
  };

  test('accounts for both keyboard height and viewport pan, retaining submit action', async () => {
    const apply = jest.fn();
    await renderModal({ onOk: apply });
    const modal = document.querySelector('.adaptive-modal .ant-modal');
    const content = modal.querySelector('.ant-modal-content');
    expect(modal.style.top).toBe('8px');
    expect(content.style.maxHeight).toBe('796px');

    await act(async () => {
      viewport.height = 320;
      viewport.offsetTop = 120;
      viewport.dispatchEvent(new Event('resize'));
      viewport.dispatchEvent(new Event('scroll'));
    });
    expect(modal.style.top).toBe('128px');
    expect(content.style.maxHeight).toBe('304px');
    const button = [...modal.querySelectorAll('.ant-modal-footer button')]
      .find(element => element.textContent === '应用筛选');
    await act(async () => button.click());
    expect(apply).toHaveBeenCalledTimes(1);
  });

  test('reopening measures the current viewport and closing removes listeners', async () => {
    const remove = jest.spyOn(viewport, 'removeEventListener');
    await renderModal();
    await renderModal({ open: false });
    expect(remove).toHaveBeenCalledWith('resize', expect.any(Function));
    expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function));
    viewport.height = 375;
    viewport.offsetTop = 30;
    await renderModal();
    expect(document.querySelector('.adaptive-modal .ant-modal').style.top).toBe('38px');
    expect(document.querySelector('.adaptive-modal .ant-modal-content').style.maxHeight).toBe('359px');
    remove.mockRestore();
  });

  test('resizing a modal does not scroll a background input', async () => {
    const background = document.createElement('input');
    background.scrollIntoView = jest.fn();
    document.body.appendChild(background);
    await renderModal();
    background.focus();
    await act(async () => {
      viewport.dispatchEvent(new Event('resize'));
      await new Promise(resolve => window.requestAnimationFrame(resolve));
    });
    expect(background.scrollIntoView).not.toHaveBeenCalled();
    background.remove();
  });
});
