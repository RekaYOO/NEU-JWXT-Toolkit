import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, QRCode, Spin } from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  pollGradeTrackingRecovery,
  startGradeTrackingRecovery,
} from '../services/api';
import './GradeTrackingRecoveryPage.css';

const GradeTrackingRecoveryPage = ({ token }) => {
  const [stage, setStage] = useState('starting');
  const [qrContent, setQrContent] = useState('');
  const [message, setMessage] = useState('正在创建 NEU Pass 二维码');
  const [secondsLeft, setSecondsLeft] = useState(300);
  const pollTimer = useRef(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const poll = useCallback(async () => {
    try {
      const result = await pollGradeTrackingRecovery(token);
      if (result.status === 'authenticated') {
        stopPolling();
        setStage('authenticated');
        setMessage('教务登录已经恢复，成绩追踪将自动继续');
      } else if (result.status === 'expired') {
        stopPolling();
        setStage('expired');
        setMessage('本次二维码已超过五分钟，请重新生成');
      } else if (result.status === 'error') {
        stopPolling();
        setStage('error');
        setMessage(result.message || '认证失败，请重新生成二维码');
      } else if (result.message) {
        setMessage(result.message);
      }
    } catch (error) {
      if (error.response?.status === 404) {
        stopPolling();
        setStage('invalid');
        setMessage('一次性登录链接不存在或已经完成使用');
      }
    }
  }, [stopPolling, token]);

  const start = useCallback(async () => {
    stopPolling();
    setStage('starting');
    setMessage('正在创建 NEU Pass 二维码');
    try {
      const result = await startGradeTrackingRecovery(token);
      setQrContent(result.qr_content);
      setSecondsLeft(result.expires_in || 300);
      setStage('pending');
      setMessage('请使用微信或 NEU Pass 扫码并确认登录');
      pollTimer.current = window.setInterval(poll, result.poll_interval * 1000 || 3000);
    } catch (error) {
      setStage(error.response?.status === 404 ? 'invalid' : 'error');
      setMessage(
        error.response?.data?.detail
        || '暂时无法创建二维码，请稍后重试'
      );
    }
  }, [poll, stopPolling, token]);

  useEffect(() => {
    start();
    return stopPolling;
  }, [start, stopPolling]);

  useEffect(() => {
    if (stage !== 'pending') return undefined;
    const countdown = window.setInterval(() => {
      setSecondsLeft((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearInterval(countdown);
  }, [stage]);

  return (
    <main className="tracking-recovery-page">
      <Card className="tracking-recovery-card">
        <div className="tracking-recovery-brand">
          <span>NEU</span>
          <SafetyCertificateOutlined />
        </div>
        <h1>恢复成绩追踪登录</h1>

        {stage === 'starting' && (
          <div className="tracking-recovery-state">
            <Spin size="large" />
            <p>{message}</p>
          </div>
        )}

        {stage === 'pending' && (
          <>
            <div className="tracking-recovery-qr">
              <QRCode value={qrContent} size={220} status="active" />
            </div>
            <div className="tracking-recovery-countdown">
              <ClockCircleOutlined />
              本次二维码剩余约 {Math.ceil(secondsLeft / 60)} 分钟
            </div>
            <Alert type="info" showIcon message={message} />
          </>
        )}

        {stage === 'authenticated' && (
          <div className="tracking-recovery-result is-success">
            <CheckCircleOutlined />
            <h2>登录已恢复</h2>
            <p>{message}</p>
            <small>此一次性链接现已失效，可以关闭页面。</small>
          </div>
        )}

        {['expired', 'error'].includes(stage) && (
          <div className="tracking-recovery-result">
            <Alert type="warning" showIcon message={message} />
            <Button type="primary" icon={<ReloadOutlined />} onClick={start}>
              重新生成二维码
            </Button>
          </div>
        )}

        {stage === 'invalid' && (
          <Alert
            type="warning"
            showIcon
            message="链接已失效"
            description={message}
          />
        )}

        <p className="tracking-recovery-footnote">
          一次性网页链接仅在登录成功后失效；每个 NEU Pass 二维码有效五分钟。
          请勿转发当前页面地址。
        </p>
      </Card>
    </main>
  );
};

export default GradeTrackingRecoveryPage;
