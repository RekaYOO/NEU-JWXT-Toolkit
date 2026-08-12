import {
  calculateContentAwareColumnWidths,
  getAcademicRuleDeficitText,
} from './academicReport';

describe('getAcademicRuleDeficitText', () => {
  test('显示可量化的学分、课程数和类别差额', () => {
    expect(getAcademicRuleDeficitText({
      remaining_credits: 0.5,
      missing_course_count: 1,
      missing_group_count: 2,
    })).toBe('还差 0.5 学分、1 门课程、2 个类别');
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
