import {
  exportTools, getExportToolAvailability, isExportToolAvailable,
} from './exportTools';

describe('export tool registry', () => {
  const festivalTool = exportTools.find(tool => tool.id === 'festival-activities');

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
