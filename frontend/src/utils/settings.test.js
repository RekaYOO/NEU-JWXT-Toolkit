import { columnSettings } from './settings';

test.each(['columnConfig', 'academicReportColumnConfig'])('reads valid legacy fields from %s despite malformed siblings', key => {
  const defaults = [{ key: 'name', visible: true, width: 100 }, { key: 'score', visible: true, width: 80 }];
  const saved = JSON.stringify([null, { key: 'score', visible: false, width: -5 }, { key: 'name', visible: 'false', width: 130 }]);
  const storageKey = `neu_toolbox:${key}`;
  localStorage.setItem(storageKey, saved);
  expect(columnSettings.load(defaults, key)).toEqual([
    { key: 'name', visible: true, width: 130 }, { key: 'score', visible: false, width: 80 },
  ]);
  expect(localStorage.getItem(storageKey)).toBe(saved);
});
