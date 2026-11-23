import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ApplicationTheme, { useAppearance } from './ApplicationTheme';
import { getAccessStatus, getAppearance, saveAppearance } from '../services/api';
import { builtinThemes } from './themeContract';
import AppearanceSettings from './AppearanceSettings';
import { TextEncoder } from 'util';

jest.mock('../services/api', () => ({ getAccessStatus: jest.fn(), getAppearance: jest.fn(), saveAppearance: jest.fn() }));

const snapshot = selected => ({ revision: 1, selected, templates: [], site_default: 'blue', effective: selected || 'blue' });
let appearance;
function Consumer() {
  appearance = useAppearance();
  return <input aria-label="业务草稿" defaultValue="尚未保存的业务内容" />;
}

describe('account appearance lifecycle', () => {
  let container; let root;
  beforeAll(() => { global.IS_REACT_ACT_ENVIRONMENT = true; });
  beforeEach(() => {
    jest.clearAllMocks();
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    getAppearance.mockResolvedValue(snapshot('blue'));
    getAccessStatus.mockResolvedValue({ site_default: 'blue' });
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
  const render = async identity => { await act(async () => root.render(<ApplicationTheme identity={identity}><Consumer /></ApplicationTheme>)); };
  const primary = () => document.documentElement.style.getPropertyValue('--color-brand');

  test('late save releases only the initiating account', async () => {
    let finishFirst, finishSecond;
    saveAppearance.mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { finishSecond = resolve; }));
    await render('first');
    await act(async () => { void appearance.persist({ selected: 'sakura', templates: [] }); });
    await render('second');
    expect(appearance.saving).toBe(false);
    await act(async () => { void appearance.persist({ selected: 'garden', templates: [] }); });
    await act(async () => finishFirst(snapshot('sakura')));
    expect(appearance.saving).toBe(true);
    expect(appearance.applied.id).toBe('blue');
    await expect(appearance.persist({ selected: 'blue', templates: [] })).rejects.toThrow('正在保存');
    await act(async () => finishSecond(snapshot('garden')));
    expect(appearance.saving).toBe(false);
    expect(appearance.applied.id).toBe('garden');
  });

  test('previous account cannot save a draft or replace a new account preview', async () => {
    await render('first');
    const previous = appearance;
    await render('second');
    await act(async () => appearance.preview(builtinThemes[0]));
    await act(async () => previous.preview(builtinThemes[2]));
    await act(async () => previous.cancelPreview());
    const reads = getAppearance.mock.calls.length;
    await act(async () => previous.reload());
    expect(getAppearance).toHaveBeenCalledTimes(reads);
    expect(primary()).toBe(builtinThemes[0].colors.primary);
    await expect(previous.persist({ selected: 'garden', templates: [] })).rejects.toThrow('账号已切换');
    expect(saveAppearance).not.toHaveBeenCalled();
  });

  test.each(['success', 'failure'])('latest imported draft survives late %s', async outcome => {
    const originalEncoder = global.TextEncoder;
    global.TextEncoder = TextEncoder;
    try {
      await act(async () => root.render(<ApplicationTheme identity="first"><AppearanceSettings /></ApplicationTheme>));
      const input = container.querySelector('input[type="file"]');
      const select = async text => {
        Object.defineProperty(input, 'files', { configurable: true, value: [{ size: 256, text }] });
        await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
      };
      let complete;
      await select(() => new Promise((resolve, reject) => {
        complete = () => outcome === 'failure' ? reject(new Error('obsolete import')) : resolve(JSON.stringify(builtinThemes[0]));
      }));
      await select(async () => JSON.stringify(builtinThemes[1]));
      await act(async () => complete());
      expect(container.querySelector('input#name').value).toBe(`${builtinThemes[1].name} 副本`);
      expect(container.textContent).not.toContain('obsolete import');
      expect(saveAppearance).not.toHaveBeenCalled();
    } finally {
      global.TextEncoder = originalEncoder;
    }
  });

  test('preview cancel and saved selection preserve business DOM and drafts', async () => {
    await render('first');
    const input = container.querySelector('input'); input.value = '编辑中';
    await act(async () => appearance.preview(builtinThemes[0]));
    expect(primary()).toBe('#a83264');
    await act(async () => appearance.cancelPreview());
    expect(primary()).toBe('#2563eb');
    saveAppearance.mockResolvedValue(snapshot('garden'));
    await act(async () => appearance.persist({ selected: 'garden', templates: [] }));
    expect(primary()).toBe('#0f766e');
    expect(container.querySelector('input')).toBe(input);
    expect(input.value).toBe('编辑中');
  });

  test('switching accounts immediately removes private state and ignores late responses', async () => {
    getAppearance.mockResolvedValue(snapshot('sakura'));
    await render('first');
    let finishSecond;
    getAppearance.mockImplementationOnce(() => new Promise(resolve => { finishSecond = resolve; }));
    await render('second');
    expect(primary()).toBe('#2563eb');
    expect(appearance.loaded).toBe(false);
    getAppearance.mockResolvedValue(snapshot('garden'));
    await render('third');
    await act(async () => finishSecond(snapshot('sakura')));
    expect(primary()).toBe('#0f766e');
    expect(appearance.identity).toBe('third');
    await render('');
    expect(primary()).toBe('#2563eb');
  });

  test('failed save leaves existing appearance and preview available to cancel', async () => {
    await render('first');
    await act(async () => appearance.preview(builtinThemes[0]));
    saveAppearance.mockRejectedValue(new Error('conflict'));
    await act(async () => { await expect(appearance.persist({ selected: 'sakura', templates: [] })).rejects.toThrow('conflict'); });
    expect(appearance.applied.id).toBe('blue');
    expect(primary()).toBe('#a83264');
    await act(async () => appearance.cancelPreview());
    expect(primary()).toBe('#2563eb');
  });

  test('public screens follow site default without reading personal templates', async () => {
    getAccessStatus.mockResolvedValue({ site_default: 'garden' });
    await render('');
    expect(primary()).toBe('#0f766e');
    expect(getAppearance).not.toHaveBeenCalled();
    expect(appearance.templates).toEqual([]);
    expect(appearance.loaded).toBe(false);
  });
});
