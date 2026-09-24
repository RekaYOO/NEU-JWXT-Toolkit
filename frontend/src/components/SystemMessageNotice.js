import React, { useEffect, useMemo, useRef } from 'react';
import { Modal } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { summarizeSystemMessages } from '../utils/systemMessageSummary';
import {
  acknowledgeAndSyncSystemMessagePrompts, unreadSystemMessagePrompts,
} from '../utils/systemMessagePrompt';
import { useCachedResource } from '../resources/ResourceStore';
import { useResourceIdentity } from '../resources/ResourceStore';
import ResourceUpdateSummary from './ResourceUpdateSummary';

const TITLES = {
  academic: '培养计划相关通知',
  timetable: '课表相关通知',
  experiment: '实验选课相关通知',
  exam: '考务相关通知',
};

const SystemMessageNotice = ({ messages = [], kind }) => {
  const account = useResourceIdentity();
  const systemMessagesResource = useCachedResource('system-messages', { autoRefresh: false });
  const messageSnapshot = systemMessagesResource.availableData?.messages || messages;
  const relevant = useMemo(
    () => unreadSystemMessagePrompts(messageSnapshot, kind, account),
    [messageSnapshot, kind, account],
  );
  const modalRef = useRef(null);
  const modalOwnerRef = useRef('');

  useEffect(() => () => {
    modalRef.current?.destroy();
    modalRef.current = null;
  }, []);

  useEffect(() => {
    const owner = `${account}:${kind}`;
    if (modalOwnerRef.current && modalOwnerRef.current !== owner) {
      modalRef.current?.destroy();
      modalRef.current = null;
    }
    modalOwnerRef.current = owner;
    if (!relevant.length) {
      modalRef.current?.destroy();
      modalRef.current = null;
      return;
    }
    if (modalRef.current) return;

    const promptSummaries = summarizeSystemMessages(relevant, kind);
    modalRef.current = Modal.info({
      title: TITLES[kind] || '教务系统通知',
      icon: <BellOutlined />,
      content: <ResourceUpdateSummary items={promptSummaries} />,
      okText: '知道了',
      maskClosable: false,
      onOk: () => {
        const acknowledged = relevant;
        modalRef.current = null;
        return acknowledgeAndSyncSystemMessagePrompts(account, kind, acknowledged)
          .then(result => {
            const marked = new Set((result?.marked || []).map(item => (
              `${item.kind || 'reminder'}:${item.message_id || ''}`
            )));
            if (!marked.size) return result;
            systemMessagesResource.updateData(current => {
              if (!current?.messages) return current;
              return {
                ...current,
                messages: current.messages.map(item => (
                  marked.has(`${item.kind || 'reminder'}:${item.id || item.message_id || ''}`)
                    ? { ...item, read: true }
                    : item
                )),
              };
            });
            return result;
          });
      },
    });
  }, [account, kind, relevant, systemMessagesResource.updateData]);

  return null;
};

export default SystemMessageNotice;
