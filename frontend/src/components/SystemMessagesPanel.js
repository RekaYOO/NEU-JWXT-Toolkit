import React from 'react';
import { Alert, Button, Card, Empty, List, Space, Tag, Typography } from 'antd';
import { useCachedResource } from '../resources/ResourceStore';

export default function SystemMessagesPanel() {
  const resource = useCachedResource('system-messages', { autoRefresh: true });
  const items = resource.availableData?.messages || [];
  return (
    <Card title="教务消息" className="system-settings-card"
      extra={<Button onClick={() => resource.refresh({ force: true })} loading={resource.loading}>刷新</Button>}>
      <Space direction="vertical" style={{ width: '100%' }}>
        {resource.error && <Alert type="warning" showIcon message="暂时无法刷新教务消息" />}
        {items.length ? <List dataSource={items} pagination={{ pageSize: 10 }}
          renderItem={item => (
            <List.Item key={`${item.kind}:${item.id}`}>
              <Space direction="vertical" size={4}>
                <Space wrap><Typography.Text strong>{item.title || '教务消息'}</Typography.Text>
                  {!item.read && <Tag color="blue">未读</Tag>}</Space>
                <Typography.Text type="secondary">{item.sent_at}</Typography.Text>
                {item.content && <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{item.content}</Typography.Paragraph>}
              </Space>
            </List.Item>
          )} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={resource.loading ? '正在加载教务消息' : '暂无教务消息'} />}
      </Space>
    </Card>
  );
}
