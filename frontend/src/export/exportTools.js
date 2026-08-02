import { TrophyOutlined } from '@ant-design/icons';

const resourceAvailable = (capabilities, resource) => {
  if (!resource) return false;
  if (capabilities?.has_festival_activities && resource === 'festival-activities') {
    return true;
  }
  return (capabilities?.resources || []).some((item) => {
    if (typeof item === 'string') return item === resource;
    const matches = item?.resource === resource || item?.name === resource || item?.id === resource;
    return matches && item?.available !== false;
  });
};

export const exportTools = [
  {
    id: 'festival-activities',
    title: '四节活动',
    description: '查看参加记录、按日期批量导出证书',
    icon: TrophyOutlined,
    path: '/export/festival-activities',
    resource: 'festival-activities',
    offlineReadable: true,
  },
];

export const getExportToolAvailability = (
  tool,
  { offlineMode = false, offlineCapabilities = {} } = {},
) => {
  if (!offlineMode) return { available: true, reason: '' };
  const available = tool.offlineReadable
    && resourceAvailable(offlineCapabilities, tool.resource);
  return {
    available,
    reason: available ? '' : '当前账号没有可读取的本地缓存',
  };
};

export const hasOfflineExportData = (capabilities = {}) => exportTools.some(
  tool => getExportToolAvailability(tool, {
    offlineMode: true,
    offlineCapabilities: capabilities,
  }).available,
);

export const isExportToolAvailable = (
  toolId,
  { offlineMode = false, offlineCapabilities = {} } = {},
) => {
  const tool = exportTools.find(item => item.id === toolId);
  return Boolean(tool && getExportToolAvailability(tool, {
    offlineMode, offlineCapabilities,
  }).available);
};
