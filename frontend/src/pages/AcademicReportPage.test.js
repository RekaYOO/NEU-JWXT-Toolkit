import { calcElectiveRemainingCredits } from './AcademicReportPage';

jest.mock('../resources/ResourceStore', () => ({
  useCachedResource: jest.fn()
}));

const electiveNode = (overrides = {}) => ({
  wid: overrides.name || 'node',
  name: '选修类别',
  path: '选修类别',
  path_array: ['选修类别'],
  requirement_type: 'elective',
  required_credits: 0,
  earned_credits: 0,
  remaining_credits: 0,
  is_completed: true,
  children: [],
  ...overrides
});

test('双重约束按子类最低差额和父类总量差额自底向上去重', () => {
  const parent = electiveNode({
    name: '通识选修类',
    required_credits: 10,
    earned_credits: 6,
    remaining_credits: 4,
    aggregate_remaining_credits: 4,
    requires_child_minimums_and_total: true,
    is_completed: false,
    children: [
      electiveNode({
        name: '带内部规则的子类',
        required_credits: 4,
        earned_credits: 2,
        remaining_credits: 2,
        is_completed: false,
        children: [
          electiveNode({
            name: '内部一',
            required_credits: 1,
            earned_credits: 1,
            remaining_credits: 0
          }),
          electiveNode({
            name: '内部二',
            required_credits: 1,
            earned_credits: 1,
            remaining_credits: 0
          })
        ]
      }),
      electiveNode({
        name: '已达标子类',
        required_credits: 4,
        earned_credits: 4,
        remaining_credits: 0
      })
    ]
  });

  // 子类自身缺 2，父类总量还缺 4；最终至少再修 4，而不是漏算为 2
  // 或重复累加为 6。
  expect(calcElectiveRemainingCredits([parent])).toBe(4);
});
