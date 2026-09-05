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
        <p>填写图片内容并获取短信，随后输入短信验证码。</p>
      </div>
    </div>

    {error && (
      <div aria-live="polite">
        <Alert type="warning" showIcon message={error} />
      </div>
    )}

    <section className="sms-auth-section">
      <div className="sms-auth-section-heading">
        <span>1</span>
        <div>
          <strong>填写图形验证码</strong>
          <small>看不清时可以刷新图片</small>
        </div>
      </div>
      <div className="captcha-auth-grid">
        <div className="captcha-auth-image-shell">
          {flow?.captcha_image ? (
            <img src={flow.captcha_image} alt="图形验证码" className="captcha-auth-image" />
          ) : (
            <span>验证码暂不可用</span>
          )}
        </div>
        <Button
          className="captcha-auth-refresh"
          icon={<ReloadOutlined />}
          loading={captchaLoading}
          onClick={onRefreshCaptcha}
          aria-label="刷新图形验证码"
        >
          刷新图片
        </Button>
        <Input
          className="captcha-auth-input"
          size="large"
          value={captchaCode}
          onChange={(event) => setCaptchaCode(event.target.value.replace(/[^0-9A-Za-z]/g, ''))}
          placeholder="请输入图片中的图形验证码"
          aria-label="图形验证码"
          inputMode="text"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          maxLength={16}
        />
      </div>
    </section>

    <section className="sms-auth-section">
      <div className="sms-auth-section-heading">
        <span>2</span>
        <div>
          <strong>填写短信验证码</strong>
          <small>{smsSent ? '短信已发送，可在未收到时重新发送' : '学校将在你确认后发送短信'}</small>
        </div>
      </div>
      <div className="sms-auth-code-row">
        <Input
          size="large"
          value={smsCode}
          onChange={(event) => setSmsCode(event.target.value.replace(/[^0-9]/g, ''))}
          placeholder="请输入短信验证码"
          aria-label="短信验证码"
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
      只有点击“获取验证码”后才会发送短信；短时间频繁发送可能受到学校限制。
    </p>
  </div>
);

export default WebVPNAuthFields;
