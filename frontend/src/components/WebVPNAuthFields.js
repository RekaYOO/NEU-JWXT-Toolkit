import React from 'react';
import { Alert, Button, Input } from 'antd';
import {
  MessageOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import './WebVPNAuthFields.css';

const WebVPNAuthFields = ({
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
  embedded = false,
}) => (
  <div className={`sms-auth-content${embedded ? ' is-embedded' : ''}`}>
    <div className="sms-auth-intro">
      <span className="sms-auth-icon-shell">
        <SafetyCertificateOutlined className="sms-auth-icon" />
      </span>
      <div>
        <strong>完成短信二次认证</strong>
        <p>先填写图片中的图形验证码，再获取短信验证码。</p>
      </div>
    </div>

    {error && <Alert type="warning" showIcon message={error} />}

    <section className="sms-auth-section">
      <div className="sms-auth-section-heading">
        <span>1</span>
        <strong>图形验证码</strong>
      </div>
      <div className="captcha-auth-row">
        <div className="captcha-auth-image-shell">
          {flow?.captcha_image ? (
            <img src={flow.captcha_image} alt="图形验证码" className="captcha-auth-image" />
          ) : (
            <span>验证码暂不可用</span>
          )}
        </div>
        <Button
          icon={<ReloadOutlined />}
          loading={captchaLoading}
          onClick={onRefreshCaptcha}
        >
          刷新图片
        </Button>
      </div>
      <Input
        size="large"
        value={captchaCode}
        onChange={(event) => setCaptchaCode(event.target.value.replace(/[^0-9A-Za-z]/g, ''))}
        placeholder="请输入图片中的图形验证码"
        inputMode="text"
        autoComplete="off"
        maxLength={16}
      />
    </section>

    <section className="sms-auth-section">
      <div className="sms-auth-section-heading">
        <span>2</span>
        <strong>短信验证码</strong>
      </div>
      <div className="sms-auth-code-row">
        <Input
          size="large"
          value={smsCode}
          onChange={(event) => setSmsCode(event.target.value.replace(/[^0-9]/g, ''))}
          placeholder="请输入短信验证码"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          onPressEnter={onVerify}
        />
        <Button
          size="large"
          icon={<MessageOutlined />}
          loading={loading}
          disabled={!captchaCode.trim()}
          onClick={onSendSMS}
        >
          {smsSent ? '重新发送' : '获取验证码'}
        </Button>
      </div>
    </section>

    <p className="sms-auth-note">
      图形验证码需手动填写；只有点击“获取验证码”后，学校才会发送短信。
      未收到短信时可再次点击“重新发送”，学校可能会限制短时间内的发送频率。
    </p>
  </div>
);

export default WebVPNAuthFields;
