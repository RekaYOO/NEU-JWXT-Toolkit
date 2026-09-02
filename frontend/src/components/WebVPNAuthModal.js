import React from 'react';
import { Alert, Button, Input, Modal } from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';

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
    <div className="sms-auth-content">
      <SafetyCertificateOutlined className="sms-auth-icon" />
      <p>请先填写图片中的图形验证码，再获取短信验证码。</p>
      {error && <Alert type="warning" showIcon message={error} />}
      {flow?.captcha_image && (
        <div className="captcha-auth-row">
          <img src={flow.captcha_image} alt="图形验证码" className="captcha-auth-image" />
          <Button loading={captchaLoading} onClick={onRefreshCaptcha}>刷新图片</Button>
        </div>
      )}
      <Input
        value={captchaCode}
        onChange={(event) => setCaptchaCode(event.target.value.replace(/[^0-9A-Za-z]/g, ''))}
        placeholder="请输入图片中的图形验证码"
        inputMode="text"
        autoComplete="off"
        maxLength={16}
      />
      <Button type="link" loading={loading} onClick={onSendSMS}>
        {smsSent ? '重新发送验证码' : '获取短信验证码'}
      </Button>
      <Input
        value={smsCode}
        onChange={(event) => setSmsCode(event.target.value)}
        placeholder="请输入短信验证码"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={8}
        onPressEnter={onVerify}
      />
      <p className="sms-auth-note">图形验证码需手动填写；短信只会在点击“获取短信验证码”后发送。</p>
    </div>
  </Modal>
);

export default WebVPNAuthModal;
