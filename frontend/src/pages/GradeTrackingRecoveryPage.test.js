import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import GradeTrackingRecoveryPage from './GradeTrackingRecoveryPage';
import {
  getGradeTrackingRecoveryStatus,
  startGradeTrackingRecovery,
} from '../services/api';

jest.mock('../services/api', () => ({
  cancelGradeTrackingRecovery: jest.fn().mockResolvedValue({ success: true }),
  getGradeTrackingRecoveryStatus: jest.fn(),
  pollGradeTrackingRecovery: jest.fn(),
  refreshGradeTrackingRecoveryCaptcha: jest.fn(),
  sendGradeTrackingRecoverySMS: jest.fn(),
  startGradeTrackingRecovery: jest.fn(),
  verifyGradeTrackingRecoverySMS: jest.fn(),
}));

describe('GradeTrackingRecoveryPage', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('后台账密已进入短信挑战时直接显示验证码，不重复创建二维码', async () => {
    getGradeTrackingRecoveryStatus.mockResolvedValue({
      status: 'sms_required',
      flow_id: 'sms-flow',
      captcha_image: 'data:image/jpeg;base64,YWJj',
      expires_in: 180,
    });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<GradeTrackingRecoveryPage token="recovery-token" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getGradeTrackingRecoveryStatus).toHaveBeenCalledWith('recovery-token');
    expect(startGradeTrackingRecovery).not.toHaveBeenCalled();
    expect(container.textContent).toContain('完成短信二次认证');
    expect(container.textContent).toContain('图形验证码');
    expect(container.textContent).toContain('获取验证码');
    expect(container.querySelector('img[alt="图形验证码"]')).not.toBeNull();

    await act(async () => root.unmount());
    container.remove();
  });
});
