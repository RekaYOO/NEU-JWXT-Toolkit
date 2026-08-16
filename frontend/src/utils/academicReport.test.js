import {
  calculateContentAwareColumnWidths,
  collectAcademicPlanDeficits,
  getAcademicRuleDeficitText,
  overlayExternalSelectedCourses,
} from './academicReport';

test('选课工作台复用培养计划缺口口径并带出待修课程分类信息', () => {
  const deficits = collectAcademicPlanDeficits([{
    wid: 'humanities', name: '人文社会科学类', path: '通识类 > 人文社会科学类',
    path_array: ['通识类', '人文社会科学类'], requirement_type: 'elective',
    required_credits: 4, earned_credits: 2, remaining_credits: 2,
    children: [], courses: [
      { course_code: 'A1', course_name: '已选课程', credit: 2, course_nature: '选修', is_selected: true, is_passed: false },
      { course_code: 'A2', course_name: '待修课程', credit: 2, course_nature: '选修', is_selected: false, is_passed: false },
    ],
  }]);

  expect(deficits).toHaveLength(1);
  expect(deficits[0]).toMatchObject({
    name: '人文社会科学类', remaining_credits: 2,
    pending_course_count: 1, pending_credits: 2,
    course_natures: ['选修'],
  });
  expect(deficits[0].unfinished_courses).toEqual([
    expect.objectContaining({ course_code: 'A2', course_name: '待修课程' }),
  ]);
});

test('培养计划缺口按必修和选修分组并保持培养计划原顺序', () => {
  const node = (wid, requirementType, remainingCredits) => ({
    wid, name: wid, path: wid, path_array: [wid], requirement_type: requirementType,
    required_credits: remainingCredits, earned_credits: 0, remaining_credits: remainingCredits,
    children: [], courses: [],
  });
  const deficits = collectAcademicPlanDeficits([
    node('选修一', 'elective', 8),
    node('必修一', 'required', 1),
    node('必修二', 'required', 6),
    node('选修二', 'elective', 2),
  ]);
  expect(deficits.map(item => item.wid)).toEqual(['必修一', '必修二', '选修一', '选修二']);
});

test('JWXK 已确认选中课程按课程代码实时扣减培养计划缺口且不重复计入', () => {
  const categories = [{
    wid: 'humanities', name: '人文社会科学类', path: '通识 > 人文社会科学类',
    path_array: ['通识', '人文社会科学类'], requirement_type: 'elective', is_leaf: true,
    required_credits: 6, selected_credits: 0, earned_credits: 2, remaining_credits: 4,
    missing_course_count: 2, children: [], courses: [
      { course_code: 'H1', course_name: '课程一', credit: 2, is_passed: true, is_selected: false },
      { course_code: 'H2', course_name: '课程二', credit: 2, is_passed: false, is_selected: false },
      { course_code: 'H3', course_name: '课程三', credit: 2, is_passed: false, is_selected: true },
    ],
  }];
  const projected = overlayExternalSelectedCourses(categories, [
    { course_code: 'H2', course_name: '课程二', credits: '2' },
    { course_code: 'H3', course_name: '课程三', credits: '2' },
  ]);
  const [category] = projected.categories;
  expect(category.remaining_credits).toBe(2);
  expect(category.external_selected_credits).toBe(2);
  expect(category.missing_course_count).toBe(1);
  expect(category.courses.find(course => course.course_code === 'H2')).toMatchObject({
    is_selected: true, selection_source: 'course_selection_realtime',
  });
  expect(projected.matched).toHaveLength(1);
});

test('计划外课程只在官方类别可可靠对应时计入，未知类别保持未匹配', () => {
  const categories = [{
    wid: 'humanities', name: '人文社会科学类', path: '通识 > 人文社会科学类',
    path_array: ['通识', '人文社会科学类'], requirement_type: 'elective', is_leaf: true,
    required_credits: 4, selected_credits: 0, earned_credits: 0, remaining_credits: 4,
    children: [], courses: [],
  }];
  const projected = overlayExternalSelectedCourses(categories, [
    { course_code: 'OUT1', course_name: '计划外人文课', credits: '2', course_category: '人文社会科学课', course_nature: '选修' },
    { course_code: 'OUT2', course_name: '无法归类课程', credits: '1', course_category: '', course_nature: '选修' },
  ]);
  expect(projected.categories[0].remaining_credits).toBe(2);
  expect(projected.matched.map(course => course.course_code)).toEqual(['OUT1']);
  expect(projected.unmatched.map(course => course.course_code)).toEqual(['OUT2']);
});

describe('getAcademicRuleDeficitText', () => {
  test('显示可量化的学分、课程数和类别差额', () => {
    expect(getAcademicRuleDeficitText({
      remaining_credits: 0.5,
      missing_course_count: 1,
      missing_group_count: 2,
    })).toBe('还差 0.5 学分、1 门课程、2 个课程组');
  });

  test('本轮已投课程会同步满足直接挂载规则的课程组数量', () => {
    const projected = overlayExternalSelectedCourses([{
      wid: 'foundation-elective', name: '选修', path: '学科基础类 > 选修',
      path_array: ['学科基础类', '选修'], requirement_type: 'elective', is_leaf: true,
      required_credits: 4, earned_credits: 0, selected_credits: 0, remaining_credits: 4,
      group_count_required: 2, group_count_taken: 0, missing_group_count: 2,
      missing_course_count: 2, is_completed: false, children: [], courses: [
        { course_code: 'F1', course_name: '基础选修一', credit: 2, is_passed: false, is_selected: false },
        { course_code: 'F2', course_name: '基础选修二', credit: 2, is_passed: false, is_selected: false },
      ],
    }], [
      { course_code: 'F1', course_name: '基础选修一', credits: 2, devoted_weight: 20 },
      { course_code: 'F2', course_name: '基础选修二', credits: 2, devoted_weight: 20 },
    ]);
    const [category] = projected.categories;
    expect(category.remaining_credits).toBe(0);
    expect(category.missing_course_count).toBe(0);
    expect(category.missing_group_count).toBe(0);
    expect(category.is_completed).toBe(true);
    expect(collectAcademicPlanDeficits(projected.categories)).toEqual([]);
  });

  test('学分已选够但课程未通过时显示待通过课程和学分', () => {
    expect(getAcademicRuleDeficitText({
      remaining_credits: 0,
      pending_course_count: 2,
      pending_credits: 4.5,
    })).toBe('学分已选够，仍有 2 门（4.5 学分）待通过');
  });

  test('无法从响应量化时明确这是教务系统的其他规则', () => {
    expect(getAcademicRuleDeficitText({ is_completed: false }))
      .toBe('学分要求已满足，教务系统判定另有规则未满足');
  });
});

describe('calculateContentAwareColumnWidths', () => {
  test('按表头和实际内容伸长列宽且不小于配置下限', () => {
    const columns = [
      { key: 'name', title: '课程名称', width: 100, hasControls: true },
      { key: 'credit', title: '学分', width: 70, hasControls: true },
    ];
    const rows = [{ name: '一门名称明显更长的课程', credit: 2 }];
    const widths = calculateContentAwareColumnWidths(
      columns,
      rows,
      (key, row) => row[key],
      value => String(value).length * 10,
    );

    expect(widths[0].width).toBe(182);
    expect(widths[1].width).toBe(92);
    expect(widths[0].width).toBeGreaterThan(widths[1].width);
  });

  test('多行单元格分别测量并取最宽一行', () => {
    const [column] = calculateContentAwareColumnWidths(
      [{ key: 'course', title: '课程名称', width: 80 }],
      [{ name: '短名称', code: 'A123456789' }],
      (_key, row) => [row.name, row.code],
      value => String(value).length * 10,
    );

    expect(column.width).toBe(140);
  });
});
