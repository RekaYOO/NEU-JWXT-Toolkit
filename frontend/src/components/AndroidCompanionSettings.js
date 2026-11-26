import React from 'react';
import { Button, Card, Space, Typography } from 'antd';
import { Link } from 'react-router-dom';
import { nativeShellInfo, openNativeCompanionSettings } from '../services/nativeBridge';

export default function AndroidCompanionSettings() {
  const shell = nativeShellInfo();
  if (!shell?.desktop_companion) return null;
  return (
    <Card title="安卓桌面与通知" className="system-settings-card">
      <Space direction="vertical" size="middle">
        <Typography.Text type="secondary">
          通知默认隐藏详情。开启消息提醒需允许系统通知，桌面课表的后台同步可单独开启。
        </Typography.Text>
        <Space wrap>
          <Button onClick={openNativeCompanionSettings}>管理小组件和通知</Button>
          {shell.kind === 'local' && <Link to="/grade-tracking">管理本机成绩追踪</Link>}
        </Space>
      </Space>
    </Card>
  );
}
