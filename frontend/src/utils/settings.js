/**
 * 前端本地设置存储工具
 * 
 * 用于保存用户界面设置（列显示、排序偏好等）到 localStorage
 * 所有设置项会在本地持久化，下次启动时自动恢复
 */

const SETTINGS_KEY_PREFIX = 'neu_toolbox:';

/**
 * 保存设置到 localStorage
 * @param {string} key - 设置项的键名（不需要前缀）
 * @param {any} value - 要保存的值（会被 JSON 序列化）
 */
export const saveSetting = (key, value) => {
  try {
    const fullKey = `${SETTINGS_KEY_PREFIX}${key}`;
    localStorage.setItem(fullKey, JSON.stringify(value));
  } catch (error) {
    console.error('保存设置失败:', error);
  }
};

/**
 * 从 localStorage 加载设置
 * @param {string} key - 设置项的键名（不需要前缀）
 * @param {any} defaultValue - 如果设置不存在时的默认值
 * @returns {any} 设置值
 */
export const loadSetting = (key, defaultValue = null) => {
  try {
    const fullKey = `${SETTINGS_KEY_PREFIX}${key}`;
    const stored = localStorage.getItem(fullKey);
    if (stored === null) {
      return defaultValue;
    }
    return JSON.parse(stored);
  } catch (error) {
    console.error('加载设置失败:', error);
    return defaultValue;
  }
};

/**
 * 删除某个设置项
 * @param {string} key - 设置项的键名
 */
export const removeSetting = (key) => {
  try {
    const fullKey = `${SETTINGS_KEY_PREFIX}${key}`;
    localStorage.removeItem(fullKey);
  } catch (error) {
    console.error('删除设置失败:', error);
  }
};

/**
 * 清空所有本应用的设置
 */
export const clearAllSettings = () => {
  try {
    Object.keys(localStorage)
      .filter(key => key.startsWith(SETTINGS_KEY_PREFIX))
      .forEach(key => localStorage.removeItem(key));
  } catch (error) {
    console.error('清空设置失败:', error);
  }
};

/**
 * 列设置相关的工具函数
 */
export const columnSettings = {
  DEFAULT_KEY: 'columnConfig',
  
  /**
   * 保存列配置
   * @param {Array} columns - 列配置数组
   * @param {string} key - 存储键名（可选，默认使用 DEFAULT_KEY）
   */
  save: (columns, key = null) => {
    const storageKey = key || columnSettings.DEFAULT_KEY;
    // 只保存关键字段，减少存储空间
    const minimalConfig = columns.map(col => ({
      key: col.key,
      visible: col.visible,
      width: col.width,
    }));
    saveSetting(storageKey, minimalConfig);
  },
  
  /**
   * 加载列配置，并与默认配置合并
   * @param {Array} defaultColumns - 默认列配置
   * @param {string} key - 存储键名（可选，默认使用 DEFAULT_KEY）
   * @returns {Array} 合并后的列配置
   */
  load: (defaultColumns, key = null) => {
    const storageKey = key || columnSettings.DEFAULT_KEY;
    const saved = loadSetting(storageKey, null);
    if (!saved || !Array.isArray(saved)) {
      return defaultColumns;
    }
    
    // 将保存的配置映射为对象便于查找
    const savedMap = new Map(saved.map(col => [col.key, col]));
    
    // 合并默认配置和保存的配置
    return defaultColumns.map(col => {
      const savedCol = savedMap.get(col.key);
      if (savedCol) {
        return {
          ...col,
          visible: savedCol.visible !== undefined ? savedCol.visible : col.visible,
          width: savedCol.width || col.width,
        };
      }
      return col;
    });
  },
  
  /**
   * 重置列配置（删除保存的设置）
   * @param {string} key - 存储键名（可选，默认使用 DEFAULT_KEY）
   */
  reset: (key = null) => {
    const storageKey = key || columnSettings.DEFAULT_KEY;
    removeSetting(storageKey);
  }
};

/**
 * 其他设置项的预设键名（方便统一管理）
 */
export const SettingKeys = {
  COLUMN_CONFIG: 'columnConfig',                    // 成绩页面列配置
  ACADEMIC_REPORT_COLUMN_CONFIG: 'academicReportColumnConfig',  // 培养计划页面列配置
  PAGE_SIZE: 'pageSize',                            // 表格分页大小
  THEME: 'theme',                                   // 主题设置
  LANGUAGE: 'language',                             // 语言设置
  // 未来可以在这里添加更多设置项
};

export default {
  saveSetting,
  loadSetting,
  removeSetting,
  clearAllSettings,
  columnSettings,
  SettingKeys,
};
