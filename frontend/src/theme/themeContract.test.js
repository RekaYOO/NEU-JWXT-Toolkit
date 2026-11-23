import { TextEncoder } from 'util';
import { builtinThemes, parseTemplate, themeTokens, themeVariables, validateTemplate } from './themeContract';

global.TextEncoder = TextEncoder;

test('all builtins satisfy text and primary contrast requirements', () => {
  builtinThemes.forEach(template => expect(validateTemplate(template, { builtin: true })).toBe(template));
});

test('imports reject remote resources, extra fields and illegible text', () => {
  const template = { ...builtinThemes[1], id: 'custom-safe' };
  expect(parseTemplate(JSON.stringify(template))).toEqual(template);
  expect(() => parseTemplate(JSON.stringify({ ...template, css: 'body { display:none }' }))).toThrow();
  expect(() => parseTemplate(JSON.stringify({ ...template, font: 'https://example.com/font' }))).toThrow();
  expect(() => parseTemplate(JSON.stringify({ ...template, colors: { ...template.colors, text: '#ffffff' } }))).toThrow(/对比度/);
  expect(() => parseTemplate(' '.repeat(16385))).toThrow(/16 KB/);
});

test('device reduced motion overrides every template', () => {
  builtinThemes.forEach(template => expect(themeTokens(template, true).token.motion).toBe(false));
  expect(themeTokens({ ...builtinThemes[0], motion: 'reduced' }, false).token.motion).toBe(false);
});

test('selected navigation stays readable without invalidating a saved template', () => {
  const base = builtinThemes[1];
  const template = { ...base, id: 'custom-navigation', colors: { ...base.colors, canvas: '#aaaaaa', muted: '#222222', primary: '#555555' } };
  expect(parseTemplate(JSON.stringify(template))).toEqual(template);
  const { token, components } = themeTokens(template, false);
  expect(components.Menu.darkItemSelectedColor).toBe(template.colors.text);
  expect(token.colorPrimary).toBe(template.colors.primary);
  for (const builtin of builtinThemes) expect(themeTokens(builtin, false).components.Menu.darkItemSelectedColor).toBe(builtin.colors.primary);
});

test('legacy business page colors follow the selected template', () => {
  const custom = { ...builtinThemes[0], id: 'custom-slate', colors: { ...builtinThemes[0].colors, text: '#182027', muted: '#434c55', canvas: '#fafafa' } };
  for (const template of [...builtinThemes, custom]) {
    const variables = themeVariables(template);
    for (const [role, names] of Object.entries({ text: ['text'], muted: ['text-secondary', 'text-tertiary', 'ink-soft'],
      canvas: ['surface-soft', 'surface-subtle'], surface: ['surface-raised'], border: ['border'], primary: ['primary', 'accent'] })) {
      for (const name of names) expect(variables[`--color-${name}`]).toBe(template.colors[role]);
    }
  }
});
