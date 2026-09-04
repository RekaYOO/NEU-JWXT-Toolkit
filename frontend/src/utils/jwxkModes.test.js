import {
  isRealJwxkTeachingClassType,
  jwxkBatchAccessMeta,
  jwxkScheduleOverlayMeta,
  jwxkSelectionMode,
} from './jwxkModes';

test('selection modes expose only their own task and participant semantics', () => {
  expect(jwxkSelectionMode('02')).toMatchObject({
    participantLabel: '已选人数',
    currentRecordTypes: ['selected'],
    taskTypes: ['selection', 'vacancy_swap'],
  });
  expect(jwxkSelectionMode('04')).toMatchObject({
    participantLabel: '已投注人数',
    currentRecordTypes: ['volunteered'],
    taskTypes: ['weight_strategy'],
  });
});

test('catalog-only scopes are never accepted as mutation types', () => {
  expect(isRealJwxkTeachingClassType('TJKC')).toBe(true);
  expect(isRealJwxkTeachingClassType('FANKC')).toBe(true);
  expect(isRealJwxkTeachingClassType('ALLKC')).toBe(false);
  expect(isRealJwxkTeachingClassType('ROUND')).toBe(false);
  expect(isRealJwxkTeachingClassType('')).toBe(false);
});

test('selection timetable overlay labels come from the shared mode dictionary', () => {
  expect(jwxkScheduleOverlayMeta('candidate').label).toBe('方案候选');
  expect(jwxkScheduleOverlayMeta('pending').label).toBe('已投权待结果');
  expect(jwxkScheduleOverlayMeta('selected', '02').label).toBe('已抢到课程');
  expect(jwxkScheduleOverlayMeta('selected', '04').label).toBe('已选课程');
});

test('batch access distinguishes account rows from public fallback rows', () => {
  expect(jwxkBatchAccessMeta({ access_scope: 'account', account_selectable: false }, true)).toMatchObject({
    kind: 'account_unavailable',
    label: '当前账号不可参与',
    canUse: false,
  });
  expect(jwxkBatchAccessMeta({ access_scope: 'public', account_selectable: false }, true)).toMatchObject({
    kind: 'public',
    label: '公开轮次',
    canUse: false,
  });
  expect(jwxkBatchAccessMeta({ access_scope: 'account', account_selectable: true }, true)).toMatchObject({
    kind: 'account_selectable',
    label: '账号可进入',
    canUse: true,
  });
  expect(jwxkBatchAccessMeta({ access_scope: 'account', account_selectable: true }, false).canUse).toBe(false);
  expect(jwxkBatchAccessMeta({ account_selectable: false }, false).kind).toBe('public');
  expect(jwxkBatchAccessMeta({ account_selectable: false }, true).kind).toBe('account_unavailable');
});
