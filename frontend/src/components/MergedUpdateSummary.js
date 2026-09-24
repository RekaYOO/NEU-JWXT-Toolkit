import React from 'react';
import ResourceUpdateSummary from './ResourceUpdateSummary';
import './MergedUpdateSummary.css';

const MergedUpdateSummary = ({ changes = [], messages = [], description = '' }) => (
  <div className="merged-update-summary">
    <ResourceUpdateSummary items={changes} label="检测到的数据变化：" />
    {messages.length > 0 && (
      <section className="merged-update-summary__messages" aria-label="教务系统相关消息">
        <strong>教务系统未读消息（供对照）</strong>
        <ul>
          {messages.slice(0, 4).map((item, index) => (
            <li key={`${index}-${item}`}>{item}</li>
          ))}
          {messages.length > 4 && <li>另有 {messages.length - 4} 条相关消息</li>}
        </ul>
      </section>
    )}
    {description && <p className="merged-update-summary__note">{description}</p>}
  </div>
);

export default MergedUpdateSummary;
