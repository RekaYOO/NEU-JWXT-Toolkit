import React, { useEffect, useMemo, useRef } from 'react';
import { Modal } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { relatedSystemMessages, summarizeSystemMessages } from '../utils/systemMessageSummary';
import ResourceUpdateSummary from './ResourceUpdateSummary';

const TITLES = {
  academic: '培养计划相关通知',
  timetable: '课表相关通知',
  experiment: '实验选课相关通知',
  exam: '考务相关通知',
};

const SystemMessageNotice = ({ messages = [], kind }) => {
  const relevant = useMemo(() => relatedSystemMessages(messages, kind), [messages, kind]);
  const seenRef = useRef(new Map());
  const initializedRef = useRef(false);
  const modalRef = useRef(null);

  useEffect(() => () => {
    modalRef.current?.destroy();
    modalRef.current = null;
  }, []);

  useEffect(() => {
    if (!messages.length) return;
    const next = new Map(relevant.map((item, index) => [
      item.id || item.message_id || `${item.title}-${index}`,
      `${item.title || ''}|${item.content || ''}|${item.sent_at || ''}`,
    ]));
    const changed = relevant.filter((item, index) => {
      const id = item.id || item.message_id || `${item.title}-${index}`;
      const fingerprint = `${item.title || ''}|${item.content || ''}|${item.sent_at || ''}`;
      return !seenRef.current.has(id) || seenRef.current.get(id) !== fingerprint;
    });
    const promptItems = initializedRef.current
      ? changed
      : relevant.filter(item => item.read === false);
    seenRef.current = next;
    initializedRef.current = true;
    if (!promptItems.length) return;

    const promptSummaries = summarizeSystemMessages(promptItems, kind);
    modalRef.current?.destroy();
    modalRef.current = Modal.info({
      title: TITLES[kind] || '教务系统通知',
      icon: <BellOutlined />,
      content: <ResourceUpdateSummary items={promptSummaries} />,
      okText: '知道了',
      maskClosable: true,
    });
  }, [kind, messages.length, relevant]);

  return null;
};

export default SystemMessageNotice;
