import React from 'react';
import { Typography } from 'antd';

const { Text } = Typography;

const ResourceUpdateSummary = ({ items = [], label = '本次后台更新包括：' }) => {
  const visibleItems = items.slice(0, 8);
  const remaining = items.length - visibleItems.length;
  return (
    <div className="resource-update-summary">
      <Text>{label}</Text>
      <ul style={{ margin: '10px 0', paddingInlineStart: 22 }}>
        {visibleItems.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
        {remaining > 0 && <li>另有 {remaining} 项变化</li>}
      </ul>
    </div>
  );
};

export default ResourceUpdateSummary;
