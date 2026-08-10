const escapeHtml = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const valueOrEmpty = value => value == null || value === '' ? '<span class="empty">暂无内容</span>' : escapeHtml(value);

const renderPresentedSection = section => {
  if (!section) return '';
  if (section.kind === 'table') return `<article><h3>${escapeHtml(section.title)}</h3><div class="table-wrap"><table><thead><tr>${(section.columns || []).map(column => `<th>${escapeHtml(column)}</th>`).join('')}</tr></thead><tbody>${(section.rows || []).map(row => `<tr>${section.columns.map(column => `<td>${valueOrEmpty(row[column])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></article>`;
  if (section.kind === 'attachments') return `<article><h3>${escapeHtml(section.title)}</h3>${section.items?.length ? `<ul>${section.items.map(item => `<li>${escapeHtml(item.name)}</li>`).join('')}</ul>` : '<span class="empty">暂无历史附件</span>'}</article>`;
  return `<article><h3>${escapeHtml(section.title)}</h3><div class="records">${(section.items || []).map(item => `<dl>${item.map(field => `<dt>${escapeHtml(field.label)}</dt><dd>${valueOrEmpty(field.value)}</dd>`).join('')}</dl>`).join('')}</div></article>`;
};

const renderOverview = overview => `<div class="badges"><span>${valueOrEmpty(overview.assessment_method)}</span><span>${valueOrEmpty(overview.grading_scale)}</span>${overview.course_nature ? `<span>${escapeHtml(overview.course_nature)}</span>` : ''}</div>
  <div class="facts"><div><small>开课单位</small><strong>${valueOrEmpty(overview.department)}</strong></div><div><small>学分</small><strong>${valueOrEmpty(overview.credits)}</strong></div><div><small>学时</small><strong>${valueOrEmpty(overview.hours)}</strong></div><div><small>适用专业</small><strong>${valueOrEmpty(overview.applicable_majors)}</strong></div></div>
  <article><h3>课程简介</h3><p>${valueOrEmpty(overview.introduction)}</p></article><article><h3>教材</h3><p>${valueOrEmpty(overview.textbooks)}</p></article>`;

export const buildCourseOutlineHtml = ({ overview, groups }) => {
  const title = `${overview.course_code || ''} ${overview.course_name || '课程大纲'}`.trim();
  const grouped = [
    ['教学内容', groups.teaching?.sections || []], ['考核与评价', groups.assessment?.sections || []], ['编制与附件', groups.governance?.sections || []],
  ];
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title><style>
  :root{font-family:"Microsoft YaHei",sans-serif;color:#172033;background:#f4f6f8}body{margin:0}.page{max-width:960px;margin:24px auto;padding:38px;background:#fff;box-shadow:0 8px 30px #1e293b18}h1{margin:0 0 8px;font-size:28px}h2{margin-top:34px;padding-bottom:9px;border-bottom:2px solid #2563eb;color:#173b75}h3{margin:0 0 12px}article{margin-top:14px;padding:18px;border:1px solid #e2e8f0;border-radius:10px}p{white-space:pre-wrap;line-height:1.75}.facts{display:grid;grid-template-columns:2fr .7fr .7fr 2fr;gap:10px;margin-top:14px}.facts div{padding:12px;background:#f8fafc;border-radius:8px}.facts small{display:block;color:#64748b;margin-bottom:5px}.facts strong{display:block}.badges{display:flex;gap:8px}.badges span{padding:4px 10px;border-radius:999px;background:#eaf2ff;color:#1d4ed8}.records{display:grid;gap:10px}dl{display:grid;grid-template-columns:150px 1fr;margin:0;background:#f8fafc;border-radius:8px;overflow:hidden}dt,dd{margin:0;padding:9px 11px;border-bottom:1px solid #e2e8f0}dt{font-weight:700;color:#64748b}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid #e2e8f0}th{background:#f8fafc}.meta,.empty{color:#94a3b8}@media(max-width:640px){.page{margin:0;padding:20px}.facts{grid-template-columns:1fr 1fr}.facts div:first-child,.facts div:last-child{grid-column:span 2}dl{grid-template-columns:1fr}dt{padding-bottom:0;border-bottom:0}}@media print{body{background:#fff}.page{margin:0;max-width:none;box-shadow:none;padding:0}h2,h3{break-after:avoid}article,dl{break-inside:avoid}}</style></head><body><main class="page"><h1>${escapeHtml(title)}</h1><p class="meta">数据来源：东北大学教务系统课程大纲查询 · 导出时实时读取 · 历史附件未内嵌</p>
  <nav><a href="#overview">课程概览</a>${grouped.map((group, index) => ` · <a href="#group-${index}">${escapeHtml(group[0])}</a>`).join('')}</nav><section id="overview"><h2>课程概览</h2>${renderOverview(overview)}</section>
  ${grouped.map(([name, sections], index) => `<section id="group-${index}"><h2>${escapeHtml(name)}</h2>${sections.length ? sections.map(renderPresentedSection).join('') : '<span class="empty">暂无内容</span>'}</section>`).join('')}</main></body></html>`;
};

export const downloadCourseOutlineHtml = data => {
  const html = buildCourseOutlineHtml(data); const blob = new Blob([html], { type: 'text/html;charset=utf-8' }); const url = URL.createObjectURL(blob); const link = document.createElement('a');
  const safe = `${data.overview.course_code || '课程'}_${data.overview.course_name || '课程大纲'}`.replace(/[\\/:*?"<>|]/g, '_');
  link.href = url; link.download = `${safe}_课程大纲.html`; link.click(); URL.revokeObjectURL(url);
};
