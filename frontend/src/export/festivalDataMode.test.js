import {
  AUTOMATIC_MODE, DATA_MODE_STORAGE_KEY, ON_DEMAND_MODE,
  festivalDataModeFromStorageEvent, persistFestivalDataMode, preferredFestivalDataMode,
} from './festivalDataMode';

describe('festival activity data mode preference', () => {
  test('defaults to automatic and restores an online on-demand preference', () => {
    expect(preferredFestivalDataMode({ getItem: () => null })).toBe(AUTOMATIC_MODE);
    expect(preferredFestivalDataMode({ getItem: () => ON_DEMAND_MODE })).toBe(ON_DEMAND_MODE);
  });

  test('forces automatic offline without overwriting the online preference', () => {
    const storage = {
      getItem: jest.fn(() => ON_DEMAND_MODE),
      setItem: jest.fn(),
    };
    expect(preferredFestivalDataMode(storage, { offlineMode: true })).toBe(AUTOMATIC_MODE);
    expect(persistFestivalDataMode(storage, AUTOMATIC_MODE, { offlineMode: true })).toBe(false);
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  test('persists a confirmed online preference', () => {
    const storage = { setItem: jest.fn() };
    expect(persistFestivalDataMode(storage, ON_DEMAND_MODE)).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith(DATA_MODE_STORAGE_KEY, ON_DEMAND_MODE);
  });

  test('recognizes only relevant cross-tab mode changes', () => {
    expect(festivalDataModeFromStorageEvent({
      key: DATA_MODE_STORAGE_KEY,
      newValue: ON_DEMAND_MODE,
    })).toBe(ON_DEMAND_MODE);
    expect(festivalDataModeFromStorageEvent({
      key: 'unrelated-preference',
      newValue: ON_DEMAND_MODE,
    })).toBeNull();
    expect(festivalDataModeFromStorageEvent({
      key: DATA_MODE_STORAGE_KEY,
      newValue: 'unknown-mode',
    })).toBeNull();
  });
});
