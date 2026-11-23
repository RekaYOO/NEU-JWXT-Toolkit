import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Select, Tag, Typography } from 'antd';
import { useAppearance } from './ApplicationTheme';
import { builtinThemes, parseTemplate, validateTemplate } from './themeContract';

const freshID = () => `custom-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
const colorNames = { primary: '主色', canvas: '页面背景', surface: '卡片与浮层', text: '正文', muted: '辅助文字', border: '边框' };

function AppearanceEditor({ appearance }) {
  const [form] = Form.useForm();
  const [editing, setEditing] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const importRef = useRef(null);
  const importSequence = useRef(0);
  const mounted = useRef(true);
  const cancelRef = useRef(appearance.cancelPreview);
  cancelRef.current = appearance.cancelPreview;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; cancelRef.current(); }; }, []);

  const run = async operation => {
    setError(''); setNotice('');
    try { await operation(); } catch (failure) {
      if (mounted.current) setError(failure.response?.status === 409
        ? '另一设备已修改主题，请重新读取设置后再保存；当前编辑内容仍保留。'
        : failure.response?.data?.detail || failure.message || '外观操作失败');
    }
  };
  const beginEdit = (template, copy) => {
    importSequence.current += 1;
    const draft = { ...template, id: copy ? freshID() : template.id, name: copy ? `${template.name.slice(0, 34)} 副本` : template.name };
    setEditing(draft);
    form.setFieldsValue(draft);
  };
  const draft = async () => {
    const values = await form.validateFields();
    return validateTemplate({ ...editing, ...values });
  };
  const apply = selected => run(async () => {
    await appearance.persist({ selected, templates: appearance.templates });
    if (mounted.current) setNotice(selected ? '主题已应用并保存到当前账号' : '已恢复为跟随站点默认主题');
  });
  const exportTemplate = template => run(async () => {
    validateTemplate(template, { builtin: builtinThemes.some(item => item.id === template.id) });
    const url = URL.createObjectURL(new Blob([JSON.stringify(template, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `toolbox-theme-${template.id}.json`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  return <section className="appearance-settings" aria-label="外观与主题模板">
    <Typography.Paragraph type="secondary">个人选择覆盖站点默认，设备的“减少动态效果”始终优先。预览不会保存，取消或离开此页会恢复原外观。模板只包含外观参数，不包含课程资料。</Typography.Paragraph>
    {appearance.error && <Alert type="error" message={appearance.error} action={<Button onClick={appearance.reload}>重新读取</Button>} />}
    {error && <div role="alert"><Alert type="error" message={error} action={<Button disabled={appearance.saving} onClick={appearance.reload}>重新读取设置</Button>} /></div>}
    {notice && <div role="status"><Alert type="success" message={notice} /></div>}
    {!appearance.loaded && !appearance.error && <div role="status">正在读取账号外观设置…</div>}
    {appearance.previewing && <Alert type="info" message="正在预览，尚未保存" action={<Button onClick={appearance.cancelPreview}>取消预览</Button>} />}
    <div className="appearance-actions">
      <Button disabled={!appearance.loaded || appearance.saving} onClick={() => apply('')}>恢复站点默认</Button>
      <Button disabled={!appearance.loaded || appearance.saving} onClick={() => beginEdit(appearance.applied, true)}>创建自定义模板</Button>
      <Button disabled={!appearance.loaded || appearance.saving} onClick={() => importRef.current?.click()}>导入主题文件</Button>
      <input ref={importRef} type="file" accept=".json,application/json" hidden aria-label="导入主题 JSON" onChange={event => {
        const file = event.target.files?.[0]; event.target.value = '';
        if (!file) return;
        const sequence = ++importSequence.current;
        run(async () => {
          if (file.size > 16384) throw new Error('主题文件不能超过 16 KB');
          let text;
          try { text = await file.text(); } catch (failure) {
            if (mounted.current && sequence === importSequence.current) throw failure;
            return;
          }
          if (!mounted.current || sequence !== importSequence.current) return;
          beginEdit(parseTemplate(text), true);
          setNotice('文件已校验，请预览后保存为自己的模板');
        });
      }} />
    </div>
    <div className="appearance-template-grid">{appearance.all.map(template => {
      const builtin = builtinThemes.some(item => item.id === template.id);
      return <article className="appearance-template" data-selected={template.id === appearance.applied.id} key={template.id}>
        <div><strong>{template.name}</strong> <Tag>{builtin ? '内置' : '个人'}</Tag>{template.id === appearance.applied.id && <Tag color="processing">当前应用</Tag>}</div>
        <div className="appearance-swatches" aria-hidden="true">{Object.values(template.colors).map((color, index) => <span key={index} style={{ backgroundColor: color }} />)}</div>
        <div className="appearance-actions">
          <Button disabled={!appearance.loaded || appearance.saving} onClick={() => run(() => appearance.preview(template))}>预览 {template.name}</Button>
          <Button disabled={!appearance.loaded || appearance.saving} onClick={() => apply(template.id)}>应用 {template.name}</Button>
          <Button disabled={!appearance.loaded || appearance.saving} onClick={() => beginEdit(template, builtin)}>{builtin ? '另存编辑' : '编辑'}</Button>
          <Button onClick={() => exportTemplate(template)}>导出</Button>
          {!builtin && <Button danger disabled={appearance.saving} onClick={() => run(async () => {
            await appearance.persist({ selected: appearance.selected === template.id ? '' : appearance.selected, templates: appearance.templates.filter(item => item.id !== template.id) });
            if (mounted.current) { if (editing?.id === template.id) setEditing(null); setNotice('模板已删除；若为当前模板，已恢复站点默认'); }
          })}>删除</Button>}
        </div>
      </article>;
    })}</div>
    {editing && <Form form={form} layout="vertical" aria-label="自定义模板编辑器">
      <Typography.Title level={4}>编辑自定义模板</Typography.Title>
      <Form.Item name="name" label="模板名称" rules={[{ required: true, whitespace: true, max: 40, message: '请输入 1～40 字名称' }]}><Input maxLength={40} /></Form.Item>
      <div className="appearance-editor">
        {Object.entries(colorNames).map(([key, label]) => <Form.Item key={key} name={['colors', key]} label={label} rules={[{ pattern: /^#[0-9a-fA-F]{6}$/, required: true, message: '请输入 #RRGGBB 颜色' }]}><Input maxLength={7} placeholder="#2563eb" /></Form.Item>)}
        <Form.Item name="font" label="字体"><Select options={[{ value: 'system', label: '系统无衬线' }, { value: 'serif', label: '本机衬线字体' }, { value: 'mono', label: '本机等宽字体' }]} /></Form.Item>
        <Form.Item name="radius" label="圆角"><InputNumber min={0} max={16} precision={0} /></Form.Item>
        <Form.Item name="decoration" label="装饰"><Select options={[{ value: 'none', label: '简洁' }, { value: 'soft', label: '轻柔阴影' }]} /></Form.Item>
        <Form.Item name="motion" label="动态效果"><Select options={[{ value: 'full', label: '标准' }, { value: 'reduced', label: '减少' }]} /></Form.Item>
      </div>
      <div className="appearance-actions">
        <Button onClick={() => run(async () => appearance.preview(await draft()))}>预览编辑结果</Button>
        <Button type="primary" loading={appearance.saving} onClick={() => run(async () => {
          const template = await draft();
          const exists = appearance.templates.some(item => item.id === template.id);
          if (!exists && appearance.templates.length >= 32) throw new Error('最多保存 32 个个人模板');
          await appearance.persist({ selected: template.id, templates: exists ? appearance.templates.map(item => item.id === template.id ? template : item) : [...appearance.templates, template] });
          if (mounted.current) { setEditing(null); setNotice('自定义模板已保存并应用'); }
        })}>保存并应用</Button>
        <Button onClick={() => { importSequence.current += 1; setEditing(null); appearance.cancelPreview(); }}>取消编辑</Button>
      </div>
    </Form>}
  </section>;
}

export default function AppearanceSettings({ active = true }) {
  const appearance = useAppearance();
  return appearance && active ? <AppearanceEditor key={appearance.identity} appearance={appearance} /> : null;
}
