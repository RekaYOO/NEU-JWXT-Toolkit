export const DATA_MODE_STORAGE_KEY = 'neu_festival_activity_data_mode';
export const AUTOMATIC_MODE = 'automatic';
export const ON_DEMAND_MODE = 'on-demand';

export const preferredFestivalDataMode = (storage, { offlineMode = false } = {}) => {
  if (offlineMode) return AUTOMATIC_MODE;
  return storage?.getItem(DATA_MODE_STORAGE_KEY) === ON_DEMAND_MODE
    ? ON_DEMAND_MODE
    : AUTOMATIC_MODE;
};

export const persistFestivalDataMode = (
  storage,
  mode,
  { offlineMode = false } = {},
) => {
  if (offlineMode) return false;
  storage?.setItem(DATA_MODE_STORAGE_KEY, mode);
  return true;
};

export const festivalDataModeFromStorageEvent = (event) => {
  if (event?.key !== DATA_MODE_STORAGE_KEY) return null;
  return event.newValue === AUTOMATIC_MODE || event.newValue === ON_DEMAND_MODE
    ? event.newValue
    : null;
};
