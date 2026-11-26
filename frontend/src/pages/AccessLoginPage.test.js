import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import AccessLoginPage from './AccessLoginPage';
import { loginAccessGateway } from '../services/api';

jest.mock('../services/api', () => ({
  loginAccessGateway: jest.fn(),
}));

describe('AccessLoginPage credential boundary', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  test('网站访问密码与 NEU 账号使用不同的自动填充分区和字段名', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<AccessLoginPage configured onSuccess={jest.fn()} />);
      await Promise.resolve();
    });

    const password = container.querySelector('input[placeholder="输入服务器访问密码"]');
    expect(password?.getAttribute('autocomplete')).toBe(
      'section-toolkit-access current-password',
    );
    expect(password?.getAttribute('id')).toContain('accessPassword');
    expect(password?.getAttribute('id')).not.toBe('password');
    expect(password?.name).toBe('toolkit_access_password');

    const onSuccess = jest.fn();
    loginAccessGateway.mockRejectedValueOnce(new Error('fixture failure')).mockResolvedValueOnce({ success: true });
    const nativeCommit = jest.fn();
    window.NeuNative = { commitCredentialAutofill: nativeCommit };
    try {
      await act(async () => root.render(<AccessLoginPage configured onSuccess={onSuccess} />));
      const submit = () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(password, 'fixture-only');
        password.dispatchEvent(new Event('input', { bubbles: true }));
        submit();
      });
      expect(nativeCommit).not.toHaveBeenCalled();
      expect(onSuccess).not.toHaveBeenCalled();
      await act(async () => submit());
      expect(nativeCommit).toHaveBeenCalledWith(expect.any(Number));
      expect(onSuccess).not.toHaveBeenCalled();
      await act(async () => window.__neuAutofillCommitted(nativeCommit.mock.calls[0][0]));
      expect(onSuccess).toHaveBeenCalledTimes(1);
    } finally {
      delete window.NeuNative;
    }

    await act(async () => root.unmount());
    container.remove();
  });
});
