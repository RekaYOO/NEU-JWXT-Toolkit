import React from 'react';
import { Button } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import { nativeShellInfo, openNativeServerSettings } from '../services/nativeBridge';

export default function NativeServerSettingsButton() {
  if (nativeShellInfo()?.kind !== 'client') return null;
  return (
    <Button type="link" icon={<SettingOutlined />} onClick={openNativeServerSettings}>
      服务端设置
    </Button>
  );
}
