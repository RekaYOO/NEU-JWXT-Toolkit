import {
  isRealJwxkTeachingClassType,
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
