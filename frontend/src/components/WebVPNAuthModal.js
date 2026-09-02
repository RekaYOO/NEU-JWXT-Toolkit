import React from 'react';
import { Modal } from 'antd';
import WebVPNAuthFields from './WebVPNAuthFields';

const WebVPNAuthModal = ({
  flow,
  captchaCode,
  setCaptchaCode,
  smsCode,
  setSmsCode,
  loading = false,
  captchaLoading = false,
  smsSent = false,
  error = '',
  onRefreshCaptcha,
  onSendSMS,
  onVerify,
  onCancel,
}) => (
  <Modal
    rootClassName="login-sms-modal"
    open={Boolean(flow)}
    title="短信二次认证"
    okText="验证并登录"
    cancelText="取消"
    confirmLoading={loading}
    onOk={onVerify}
    onCancel={onCancel}
    destroyOnHidden
  >
    <WebVPNAuthFields
      flow={flow}
      captchaCode={captchaCode}
      setCaptchaCode={setCaptchaCode}
      smsCode={smsCode}
      setSmsCode={setSmsCode}
      loading={loading}
      captchaLoading={captchaLoading}
      smsSent={smsSent}
      error={error}
      onRefreshCaptcha={onRefreshCaptcha}
      onSendSMS={onSendSMS}
      onVerify={onVerify}
    />
  </Modal>
);

export default WebVPNAuthModal;
