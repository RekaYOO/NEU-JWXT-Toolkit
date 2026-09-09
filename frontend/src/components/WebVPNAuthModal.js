import React, { useEffect, useState } from 'react';
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
  zIndex,
}) => {
  const [viewportHeight, setViewportHeight] = useState(null);

  useEffect(() => {
    if (!flow || typeof window === 'undefined') return undefined;
    const viewport = window.visualViewport;
    const update = () => setViewportHeight(Math.round(viewport?.height || window.innerHeight));
    update();
    viewport?.addEventListener('resize', update);
    viewport?.addEventListener('scroll', update);
    window.addEventListener('resize', update);
    return () => {
      viewport?.removeEventListener('resize', update);
      viewport?.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
    };
  }, [flow]);

  return (
    <Modal
      rootClassName="login-sms-modal"
      zIndex={zIndex}
      open={Boolean(flow)}
      title={flow?.sms_verified ? '建立教务会话' : '短信二次认证'}
      okText={flow?.sms_verified ? '继续建立会话' : '验证并登录'}
      cancelText="取消"
      confirmLoading={loading}
      okButtonProps={{ disabled: !flow?.sms_verified && !String(smsCode || '').trim() }}
      onOk={onVerify}
      onCancel={onCancel}
      destroyOnHidden
      style={viewportHeight ? { '--auth-viewport-height': `${viewportHeight}px` } : undefined}
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
};

export default WebVPNAuthModal;
