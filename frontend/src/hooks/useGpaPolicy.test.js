import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import useGpaPolicy from './useGpaPolicy';
import { getGpaPolicy, saveGpaPolicy } from '../services/api';

let mockIdentity = '20250001';
jest.mock('../resources/ResourceStore', () => ({ useResourceIdentity: () => mockIdentity }));
jest.mock('../services/api', () => ({ getGpaPolicy: jest.fn(), saveGpaPolicy: jest.fn() }));

const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

describe('account GPA policy', () => {
  let root, container, state;
  function Harness({ offline = false }) {
    state = useGpaPolicy(offline);
    return null;
  }
  const render = async offline => act(async () => root.render(<Harness offline={offline} />));
  beforeEach(() => {
    jest.resetAllMocks();
    mockIdentity = '20250001';
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    getGpaPolicy.mockResolvedValue({ mode: 'from_2025' });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('save failures retain the last authoritative policy', async () => {
    await render();
    saveGpaPolicy.mockRejectedValue(new Error('failed'));
    await act(async () => { await expect(state.save('through_2024')).rejects.toThrow('failed'); });
    expect(state.policy.mode).toBe('from_2025');
    expect(state.saving).toBe(false);
  });

  test('offline mode reads the saved policy and never writes', async () => {
    await render(true);
    await act(async () => state.save('through_2024'));
    expect(getGpaPolicy).toHaveBeenCalledWith(true);
    expect(saveGpaPolicy).not.toHaveBeenCalled();
  });

  test('an old cache read cannot replace a completed save', async () => {
    await render();
    const pending = deferred();
    getGpaPolicy.mockReturnValueOnce(pending.promise);
    let request;
    await act(async () => { request = state.reload(); });
    saveGpaPolicy.mockResolvedValue({ mode: 'through_2024' });
    await act(async () => state.save('through_2024'));
    await act(async () => { pending.resolve({ mode: 'from_2025' }); await request; });
    expect(state.policy.mode).toBe('through_2024');
  });

  test('account switches discard late preference saves', async () => {
    await render();
    const pending = deferred();
    saveGpaPolicy.mockReturnValueOnce(pending.promise);
    let request;
    await act(async () => { request = state.save('through_2024'); });
    mockIdentity = '20260001';
    await render();
    await act(async () => { pending.resolve({ mode: 'through_2024' }); await request; });
    expect(state.policy.mode).toBe('from_2025');
    expect(state.saving).toBe(false);
  });
});
