import {
  ASYNC_ROUTE_DEFINITIONS,
  ASYNC_ROUTE_IDS,
  createRouteModuleRegistry,
  routeIdForPath,
} from './routeModules';

describe('route module registry', () => {
  test('deduplicates concurrent loads and retains successful modules', async () => {
    const importer = jest.fn().mockResolvedValue({ default: 'page' });
    const registry = createRouteModuleRegistry({ page: importer });
    const [first, second] = await Promise.all([
      registry.page.load(),
      registry.page.load(),
    ]);
    expect(first).toEqual({ default: 'page' });
    expect(second).toBe(first);
    expect(importer).toHaveBeenCalledTimes(1);
    await registry.page.load();
    expect(importer).toHaveBeenCalledTimes(1);
  });

  test('clears rejected promises so a later load can recover', async () => {
    const importer = jest.fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ default: 'page' });
    const registry = createRouteModuleRegistry({ page: importer });
    await expect(registry.page.load()).rejects.toThrow('network');
    await expect(registry.page.load()).resolves.toEqual({ default: 'page' });
    expect(importer).toHaveBeenCalledTimes(2);
  });

  test('maps nested route families to their shared bundles', () => {
    expect(routeIdForPath('/course-selection')).toBe('course-selection');
    expect(routeIdForPath('/course-selection/ROUND-1/catalog')).toBe('course-selection-workspace');
    expect(routeIdForPath('/course-selection/archive/ARCHIVE-1')).toBe('course-selection-archive');
    expect(routeIdForPath('/export/festival-activities')).toBe('festival-activities');
    expect(routeIdForPath('/timetable')).toBeNull();
  });

  test('maps every declared async route and keeps planned bundle groups together', () => {
    const concretePaths = {
      'course-selection-workspace': '/course-selection/ROUND-1/catalog',
      'course-selection-archive': '/course-selection/archive/ARCHIVE-1',
    };
    ASYNC_ROUTE_IDS.forEach(routeId => {
      const definition = ASYNC_ROUTE_DEFINITIONS[routeId];
      const pathname = concretePaths[routeId] || definition.path;
      expect(routeIdForPath(pathname)).toBe(routeId);
    });
    expect(ASYNC_ROUTE_DEFINITIONS['grade-tracking'].bundle).toBe('academic');
    expect(ASYNC_ROUTE_DEFINITIONS['academic-report'].bundle).toBe('academic');
    expect(ASYNC_ROUTE_DEFINITIONS['course-selection'].bundle).toBe('courseSelection');
    expect(ASYNC_ROUTE_DEFINITIONS['course-selection-workspace'].bundle).toBe('courseSelection');
    expect(ASYNC_ROUTE_DEFINITIONS['course-selection-archive'].bundle).toBe('courseSelection');
    expect(ASYNC_ROUTE_DEFINITIONS.export.bundle).toBe('export');
    expect(ASYNC_ROUTE_DEFINITIONS['festival-activities'].bundle).toBe('export');
    expect(ASYNC_ROUTE_DEFINITIONS['academic-documents'].bundle).toBe('export');
  });
});
