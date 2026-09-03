import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import AccessLoginPage from './AccessLoginPage';

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

    await act(async () => root.unmount());
    container.remove();
  });
});
