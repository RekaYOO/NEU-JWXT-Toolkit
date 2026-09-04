export const JWXK_SELECTION_MODES = {
  '02': {
    code: '02',
    label: '抢选',
    participantField: 'selected_count',
    participantLabel: '已选人数',
    currentRecordTypes: ['selected'],
    taskTypes: ['selection', 'vacancy_swap'],
    taskEmptyText: '尚未创建自动抢课或空位追踪任务',
  },
  '04': {
    code: '04',
    label: '权重',
    participantField: 'weight_participant_count',
    participantLabel: '已投注人数',
    currentRecordTypes: ['volunteered'],
    taskTypes: ['weight_strategy'],
    taskEmptyText: '尚未创建策略投权任务',
  },
};

export const jwxkSelectionMode = code => JWXK_SELECTION_MODES[String(code || '')] || {
  code: String(code || ''),
  label: '选课',
  participantField: 'selected_count',
  participantLabel: '已选人数',
  currentRecordTypes: ['selected'],
  taskTypes: [],
  taskEmptyText: '尚未创建自动任务',
};

export const isRealJwxkTeachingClassType = value => (
  !['', 'ALL', 'ROUND', 'ALLKC'].includes(String(value || '').trim().toUpperCase())
);

export const jwxkScheduleOverlayMeta = (layer, selectionTypeCode = '') => ({
  preview: { label: '正在预览', color: '#2563eb' },
  candidate: { label: '方案候选', color: '#2563eb' },
  pending: { label: '已投权待结果', color: '#2563eb' },
  selected: {
    label: String(selectionTypeCode || '') === '02' ? '已抢到课程' : '已选课程',
    color: '#16a34a',
  },
}[layer] || { label: '待选课程', color: '#2563eb' });

export const jwxkBatchAccessMeta = (batch = {}, serviceAuthenticated = false) => {
  const accessScope = batch.access_scope === 'public' || batch.access_scope === 'account'
    ? batch.access_scope
    : serviceAuthenticated ? 'account' : 'public';
  if (accessScope === 'public') {
    return {
      kind: 'public',
      label: '公开轮次',
      tagColor: 'default',
      canUse: false,
    };
  }
  if (!batch.account_selectable) {
    return {
      kind: 'account_unavailable',
      label: '当前账号不可参与',
      tagColor: 'warning',
      canUse: false,
    };
  }
  return {
    kind: 'account_selectable',
    label: '账号可进入',
    tagColor: 'processing',
    canUse: Boolean(serviceAuthenticated),
  };
};
