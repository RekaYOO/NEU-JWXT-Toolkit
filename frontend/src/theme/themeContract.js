import sakura from './templates/sakura.json';
import blue from './templates/blue.json';
import garden from './templates/garden.json';

export const builtinThemes = [sakura, blue, garden];

export const fonts = {
  system: "'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif",
  serif: "'Noto Serif CJK SC', 'Songti SC', SimSun, serif",
  mono: "Consolas, 'Courier New', monospace",
};

const exactKeys = (object, keys) => object && typeof object === 'object' && !Array.isArray(object)
  && Object.keys(object).length === keys.length && keys.every(key => Object.hasOwn(object, key));
const luminance = color => {
  const channels = color.slice(1).match(/../g).map(value => parseInt(value, 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
};
export const contrast = (first, second) => (Math.max(luminance(first), luminance(second)) + 0.05)
  / (Math.min(luminance(first), luminance(second)) + 0.05);

export function validateTemplate(template, { builtin = false } = {}) {
  if (!exactKeys(template, ['schema_version', 'id', 'name', 'colors', 'font', 'radius', 'decoration', 'motion'])
    || template.schema_version !== 1 || typeof template.id !== 'string'
    || !(builtin ? builtinThemes.some(item => item.id === template.id) : /^custom-[a-zA-Z0-9_-]{1,64}$/.test(template.id))
    || typeof template.name !== 'string' || template.name.trim() !== template.name
    || [...template.name].length < 1 || [...template.name].length > 40 || /[\u0000-\u001f\u007f-\u009f]/.test(template.name)
    || !Object.hasOwn(fonts, template.font) || !Number.isInteger(template.radius) || template.radius < 0 || template.radius > 16
    || !['none', 'soft'].includes(template.decoration) || !['full', 'reduced'].includes(template.motion)
    || !exactKeys(template.colors, ['primary', 'canvas', 'surface', 'text', 'muted', 'border'])
    || Object.values(template.colors).some(value => typeof value !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(value))) {
    throw new Error('主题文件格式无效，仅支持版本 1 的颜色、字体、圆角、装饰和动效设置');
  }
  const colors = template.colors;
  if ([[colors.text, colors.canvas], [colors.text, colors.surface], [colors.muted, colors.canvas],
    [colors.muted, colors.surface], [colors.primary, '#ffffff'], [colors.primary, colors.surface]]
    .some(([first, second]) => contrast(first, second) < 4.5)) {
    throw new Error('文字或主色对比度不足，请调整至至少 4.5:1');
  }
  return template;
}

export function parseTemplate(text) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).length > 16384) throw new Error('主题文件不能超过 16 KB');
  const template = JSON.parse(text);
  return validateTemplate(template, { builtin: builtinThemes.some(item => item.id === template.id) });
}

export function themeTokens(template, reducedMotion) {
  const { colors, radius } = template;
  const selectedNavigationColor = contrast(colors.primary, colors.canvas) >= 4.5 ? colors.primary : colors.text;
  return {
    token: { colorPrimary: colors.primary, colorInfo: colors.primary, colorText: colors.text,
      colorSuccess: '#15803d', colorWarning: '#92400e', colorError: '#b91c1c',
      colorTextPlaceholder: colors.muted,
      colorTextSecondary: colors.muted, colorTextDisabled: colors.muted, colorBorder: colors.border,
      colorBorderSecondary: colors.border, colorBgLayout: colors.canvas, colorBgContainer: colors.surface,
      colorBgElevated: colors.surface, fontFamily: fonts[template.font], borderRadius: radius,
      borderRadiusLG: radius + 2, motion: !reducedMotion && template.motion !== 'reduced' },
    components: {
      Button: { borderRadius: radius, primaryShadow: 'none', defaultShadow: 'none' },
      Card: { borderRadiusLG: radius + 2, headerBg: colors.surface },
      Menu: { darkItemBg: colors.surface, darkSubMenuItemBg: colors.surface, darkItemColor: colors.text,
        darkItemHoverBg: colors.canvas, darkItemSelectedBg: colors.canvas, darkItemSelectedColor: selectedNavigationColor, itemBorderRadius: radius },
      Table: { headerBg: colors.canvas, headerColor: colors.text, headerSplitColor: colors.border, rowHoverBg: colors.canvas, borderColor: colors.border },
      Tabs: { itemSelectedColor: colors.primary, inkBarColor: colors.primary },
    },
  };
}

export function themeVariables(template) {
  const { colors, radius } = template;
  return { '--color-brand': colors.primary, '--color-brand-strong': colors.primary, '--color-brand-panel': colors.primary,
    '--color-primary': colors.primary, '--color-accent': colors.primary, '--color-border': colors.border,
    '--color-text': colors.text, '--color-text-secondary': colors.muted, '--color-text-tertiary': colors.muted,
    '--color-ink-soft': colors.muted, '--color-surface-soft': colors.canvas,
    '--color-surface-subtle': colors.canvas, '--color-surface-raised': colors.surface,
    '--color-success': '#15803d', '--color-warning': '#92400e', '--color-danger': '#b91c1c',
    '--color-ink': colors.text, '--color-muted': colors.muted, '--color-subtle': colors.muted,
    '--color-line': colors.border, '--color-line-soft': colors.border, '--color-canvas': colors.canvas,
    '--color-surface': colors.surface, '--color-surface-muted': colors.canvas, '--color-nav': colors.surface,
    '--color-nav-hover': colors.canvas, '--color-nav-text': colors.text, '--color-nav-muted': colors.muted,
    '--font-sans': fonts[template.font], '--radius-sm': `${Math.max(0, radius - 2)}px`,
    '--radius-md': `${radius}px`, '--radius-lg': `${radius + 2}px`,
    '--shadow-sm': template.decoration === 'soft' ? '0 1px 3px #00000012' : 'none',
    '--shadow-md': template.decoration === 'soft' ? '0 8px 24px #00000014' : 'none' };
}
