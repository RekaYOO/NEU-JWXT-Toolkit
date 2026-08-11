import {
  exportTools, getExportToolAvailability, isExportToolAvailable,
} from './exportTools';

describe('export tool registry', () => {
  const festivalTool = exportTools.find(tool => tool.id === 'festival-activities');
  const academicDocumentsTool = exportTools.find(tool => tool.id === 'academic-documents');

  test('registers official academic documents as an online-only nested tool', () => {
    expect(academicDocumentsTool.path).toBe('/export/academic-documents');
    expect(academicDocumentsTool.offlineReadable).toBe(false);
    expect(getExportToolAvailability(academicDocumentsTool).available).toBe(true);
    expect(getExportToolAvailability(academicDocumentsTool, { offlineMode: true })).toEqual({
      available: false,
      reason: '证明文件需要连接教务系统实时生成',
    });
  });

  test('registers festival activities with a stable nested route', () => {
    expect(festivalTool.path).toBe('/export/festival-activities');
    expect(festivalTool.resource).toBe('festival-activities');
    expect(festivalTool.offlineReadable).toBe(true);
  });

  test('enables installed tools online and requires their own cache offline', () => {
    expect(getExportToolAvailability(festivalTool).available).toBe(true);
    expect(isExportToolAvailable('festival-activities', {
      offlineMode: true,
      offlineCapabilities: { resources: ['some-future-export'] },
    })).toBe(false);
    expect(isExportToolAvailable('festival-activities', {
      offlineMode: true,
      offlineCapabilities: { resources: ['festival-activities'] },
    })).toBe(true);
  });
});
