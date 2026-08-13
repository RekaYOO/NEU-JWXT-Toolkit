import {
  featureAvailable,
  offlineDefaultPath,
  visibleMenuItems,
} from './featureRegistry';

describe('feature registry', () => {
  test('online mode exposes every registered feature', () => {
    expect(featureAvailable('evaluation')).toBe(true);
    expect(featureAvailable('timetable')).toBe(true);
    expect(visibleMenuItems().find(item => item.key === '/timetable')?.label).toBe('查询课表');
    expect(visibleMenuItems().find(item => item.key === '/course-selection')?.label).toBe('选课系统');
    expect(visibleMenuItems().length).toBeGreaterThan(5);
  });

  test('offline mode exposes only proven local capabilities', () => {
    const capabilities = { has_report: true, resources: [] };
    expect(featureAvailable('academic-report', {
      offlineMode: true,
      offlineCapabilities: capabilities,
    })).toBe(true);
    expect(featureAvailable('scores', {
      offlineMode: true,
      offlineCapabilities: capabilities,
    })).toBe(false);
    expect(featureAvailable('export', {
      offlineMode: true,
      offlineCapabilities: capabilities,
    })).toBe(false);
    expect(featureAvailable('timetable', {
      offlineMode: true,
      offlineCapabilities: capabilities,
    })).toBe(false);
    expect(visibleMenuItems({
      offlineMode: true,
      offlineCapabilities: capabilities,
    }).map(item => item.key)).toEqual(['/academic-report']);
  });

  test('offline default follows the registry priority', () => {
    expect(offlineDefaultPath({ has_research: true })).toBe('/research-training');
    expect(offlineDefaultPath({ resources: [] })).toBe('/login');
  });
});
