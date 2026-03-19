# 培养计划数据结构详解

本文档详细解析培养计划（学业监测报告）的数据结构、处理逻辑和常见陷阱。

---

## 1. 真实数据结构示例

### 1.1 完整响应示例

```json
{
  "student_name": "张三",
  "student_id": "20240001",
  "program_code": "CS2024",
  "program_name": "计算机科学与技术专业2024版",
  "calculated_time": "2024-03-18T10:30:00",
  "credit_summary": {
    "total_required": 160.0,
    "total_passed": 85.5,
    "total_selected": 15.0,
    "total_earned": 100.5,
    "total_remaining": 59.5,
    "completion_rate": 62.8
  },
  "categories": [
    {
      "wid": "a1b2c3d4",
      "name": "通识类",
      "category_code": "A0",
      "depth": 0,
      "path": "通识类",
      "path_array": ["通识类"],
      "is_leaf": false,
      "has_children": true,
      "required_credits": 73.0,
      "passed_credits": 45.0,
      "selected_credits": 8.0,
      "earned_credits": 53.0,
      "planned_credits": 20.0,
      "remaining_credits": 20.0,
      "completion_rate": 72.6,
      "is_completed": false,
      "courses": [],
      "children": [
        {
          "wid": "e5f6g7h8",
          "name": "数学与自然科学类",
          "depth": 1,
          "path": "通识类 > 数学与自然科学类",
          "path_array": ["通识类", "数学与自然科学类"],
          "is_leaf": true,
          "has_children": false,
          "required_credits": 20.0,
          "passed_credits": 18.0,
          "selected_credits": 2.0,
          "earned_credits": 20.0,
          "remaining_credits": 0.0,
          "completion_rate": 100.0,
          "is_completed": true,
          "courses": [
            {
              "course_name": "高等数学①㈠",
              "course_code": "MATH101",
              "course_nature": "必修",
              "credit": 5.0,
              "score": "90",
              "is_passed": true,
              "is_selected": false,
              "is_planned": false,
              "status": "01",
              "status_display": "已通过",
              "term_code": "2024-2025-1",
              "select_term_code": "2024-2025-1",
              "exam_type": "考试",
              "is_core": false,
              "category_path": ["通识类", "数学与自然科学类"],
              "category_name": "数学与自然科学类"
            }
          ],
          "children": []
        }
      ]
    }
  ],
  "outside_courses": []
}
```

---

## 2. 核心处理逻辑

### 2.1 课程状态判定（关键！）

后端返回的 `is_passed` 可能是字符串 `"是"` 或布尔值 `true`，必须统一处理：

```python
# neu_storage/integration.py
def is_course_passed(course) -> bool:
    """判断课程是否已通过"""
    if isinstance(course.is_passed, str):
        return course.is_passed == "是"
    return bool(course.is_passed)

def is_course_selected(course) -> bool:
    """判断课程是否已选课但未通过"""
    return not is_course_passed(course) and course.status == "已选课"

def is_course_planned(course) -> bool:
    """判断课程是否未修读"""
    return not is_course_passed(course) and not is_course_selected(course)
```

前端同样需要处理：

```javascript
// AcademicReportPage.js / GPACalculator.js
const isPassed = (course) => {
  if (typeof course.is_passed === 'boolean') {
    return course.is_passed;
  }
  return course.is_passed === '是' || course.is_passed === '通过';
};

const getStatusDisplay = (course) => {
  if (isPassed(course)) return '已通过';
  if (course.status === '03' || course.status === '已选课') return '已选课';
  return '未修读';
};
```

### 2.2 学分计算逻辑（复杂！）

#### 三种节点类型

1. **叶节点**（无子节点）：直接统计自己的课程
2. **实际类别**（有子节点且 `required_credits > 0`）：如"科学素养类"
3. **虚拟父节点**（有子节点但 `required_credits = 0`）：如"通识类"

```python
# 判断逻辑
is_leaf = len(cat.children) == 0
is_virtual_parent = (not is_leaf) and (total_required == 0)
is_actual_category = (not is_leaf) and (total_required > 0)
```

#### 学分汇总规则

```python
# 1. 叶节点：直接累加自己的课程
node_passed = sum(c.credit for c in cat.courses if is_passed(c))

# 2. 父节点：累加子节点的学分，但要防止超额
for child in cat.children:
    child_earned = child['earned_credits']
    child_required = child['required_credits']
    
    if child_required > 0 and child_earned > child_required:
        # 子节点超额，按比例限制
        ratio = child_required / child_earned
        children_passed += child['passed_credits'] * ratio
    else:
        # 未超额，直接使用
        children_passed += child['passed_credits']

# 3. 虚拟父节点：学分强制为0（由子节点显示）
if is_virtual_parent:
    total_passed = 0
    total_selected = 0
    total_earned = 0
```

### 2.3 必修/选修判定

```javascript
// AcademicReportPage.js
const isElectiveCategory = (node) => {
  if (!node.path_array?.length) return false;
  const pathStr = node.path_array.join(' > ');
  // 注意：通识选修也算选修！
  return node.name === '选修' || pathStr.includes('选修') || pathStr.includes('通识选修');
};

const isRequiredCategory = (node) => {
  if (!node.path_array?.length) return false;
  const pathStr = node.path_array.join(' > ');
  // 注意：通识类下的必修不算作专业必修！
  if (pathStr.includes('通识')) return false;
  return node.name === '必修' || pathStr.includes('必修');
};
```

### 2.4 显示名称处理

当节点名称是"必修"或"选修"时，应该显示父节点名称：

```javascript
const getCategoryDisplayName = (node) => {
  if (node.name !== '必修' && node.name !== '选修') {
    return node.name;
  }
  // 取父节点名称
  if (node.path_array?.length >= 2) {
    return node.path_array[node.path_array.length - 2];
  }
  return node.name;
};
```

---

## 3. 前端处理函数

### 3.1 收集所有课程（递归）

```javascript
const collectAllCourses = (categories) => {
  const courses = [];
  const traverse = (nodes) => {
    nodes.forEach(node => {
      if (node.courses?.length > 0) {
        courses.push(...node.courses.map(c => ({
          ...c,
          category_name: node.name,
          category_path: node.path,
          _id: `${c.course_code}-${c.term_code || 'none'}-${Math.random().toString(36).substr(2, 9)}`
        })));
      }
      if (node.children?.length > 0) {
        traverse(node.children);
      }
    });
  };
  traverse(categories);
  return courses;
};
```

### 3.2 根据路径过滤课程

```javascript
const filterCoursesByPath = (categories, path) => {
  const courses = [];
  const traverse = (nodes) => {
    nodes.forEach(node => {
      if (node.path === path || path === 'all') {
        // 收集此节点及其所有子节点的课程
        const collectNodeCourses = (n) => {
          if (n.courses) {
            courses.push(...n.courses.map(c => ({
              ...c,
              category_name: n.name,
              category_path: n.path,
            })));
          }
          n.children?.forEach(collectNodeCourses);
        };
        collectNodeCourses(node);
      } else if (node.children) {
        traverse(node.children);
      }
    });
  };
  traverse(categories);
  return courses;
};
```

### 3.3 查找还差学分的类别

```javascript
const findIncompleteCategories = (categories, filterFn) => {
  const result = [];
  
  const traverse = (nodes, parentNode = null) => {
    nodes.forEach(node => {
      if (!filterFn(node)) {
        // 不是目标类别，继续遍历子节点
        if (node.children) {
          traverse(node.children, node);
        }
        return;
      }
      
      // 是目标类别，且要求学分>0的叶节点
      const isLeaf = !node.children || node.children.length === 0;
      
      if (node.required_credits > 0 && isLeaf && node.remaining_credits > 0) {
        result.push({
          wid: node.wid,
          name: getCategoryDisplayName(node),
          path: node.path,
          path_array: node.path_array,
          required_credits: node.required_credits,
          earned_credits: node.earned_credits,
          remaining_credits: node.remaining_credits,
        });
      }
      
      // 继续遍历子节点
      if (node.children) {
        traverse(node.children, node);
      }
    });
  };
  
  traverse(categories);
  return result.sort((a, b) => b.remaining_credits - a.remaining_credits);
};

// 使用示例
const electiveStats = findIncompleteCategories(categories, isElectiveCategory);
const requiredStats = findIncompleteCategories(categories, isRequiredCategory);
```

---

## 4. 后端数据处理

### 4.1 原始数据解析

```python
# neu_academic/report.py
def _parse_course_from_check(self, course_data: Dict, category_name: str = "", nature_hint: str = "") -> Optional[CourseInfo]:
    course = CourseInfo()
    
    # 基本信息
    course.course_name = course_data.get("courseName", "")
    course.course_code = course_data.get("courseId", "")
    
    # 课程性质转换（01=必修，02=选修）
    nature_code = course_data.get("courseNature", "")
    if nature_code == "01":
        course.course_nature = "必修"
    elif nature_code == "02":
        course.course_nature = "选修"
    elif nature_hint:  # 从父节点继承
        course.course_nature = nature_hint
    
    # 状态转换（01=通过，03=已选课，04=未修读）
    status_code = course_data.get("status", "")
    status_map = {
        "01": "通过",
        "02": "挂科",
        "03": "已选课",
        "04": "未修读",
    }
    course.status = status_map.get(status_code, status_code)
    
    # passed 字段是布尔值
    course.is_passed = "是" if course_data.get("passed", False) else "否"
    
    return course
```

### 4.2 字典转换关键点

```python
# neu_storage/integration.py
def _report_to_dict(self, report) -> Dict[str, Any]:
    # 注意：课程状态判断必须使用 is_course_passed() 函数
    # 不能直接依赖 course.is_passed 字段
    
    courses = [
        {
            "is_passed": is_course_passed(c),  # 统一转换为布尔值
            "is_selected": is_course_selected(c),
            "is_planned": is_course_planned(c),
            "status_display": get_course_status_display(c),
        }
        for c in cat.courses
    ]
```

---

## 5. 常见问题与解决方案

### 问题1：课程状态显示错误

**现象**：已通过的课程显示为"未修读"

**原因**：`is_passed` 后端返回可能是字符串 `"是"`，直接判断 `if course.is_passed:` 会为真，但某些情况下可能是 `"否"` 或其他值。

**解决**：
```javascript
// 不要这样
if (course.is_passed) { ... }  // 错误！

// 要这样
const isPassed = typeof course.is_passed === 'boolean' 
  ? course.is_passed 
  : course.is_passed === '是';
```

### 问题2：学分统计重复

**现象**：总学分超过了实际要求学分

**原因**：父节点和子节点的学分被重复累加。

**解决**：只统计叶节点或正确处理虚拟父节点：
```javascript
// 只统计叶节点
const sumLeafCredits = (categories) => {
  let total = 0;
  const traverse = (nodes) => {
    nodes.forEach(node => {
      if (node.is_leaf) {
        total += node.earned_credits || 0;
      }
      node.children?.forEach(traverse);
    });
  };
  traverse(categories);
  return total;
};
```

### 问题3：选修/必修分类错误

**现象**：通识选修课被算作专业选修课

**原因**：判断逻辑没有排除通识类。

**解决**：
```javascript
// 错误的判断
const isElective = node.name.includes('选修');

// 正确的判断
const isElective = (node) => {
  const path = node.path_array?.join(' > ') || '';
  // 通识类下的选修是"通识选修"，不是"专业选修"
  if (path.includes('通识')) return false;
  return node.name === '选修' || path.includes('选修');
};
```

### 问题4：学期代码显示不正确

**现象**：显示 "2024-2025-1" 而不是 "2024-2025学年秋季学期"

**解决**：
```javascript
const formatTermCode = (termCode) => {
  if (!termCode) return '-';
  const match = termCode.match(/(\d{4})-(\d{4})-(\d)/);
  if (match) {
    const [, startYear, endYear, term] = match;
    const termName = term === '1' ? '秋季' : '春季';
    return `${startYear}-${endYear}学年${termName}学期`;
  }
  return termCode;
};
```

### 问题5：导入培养计划时课程状态不对

**现象**：从培养计划导入的课程，已通过的也被导入了

**原因**：没有正确过滤已通过的课程。

**解决**：
```javascript
const getPlannedCourses = () => {
  return academicPlan.filter(plan => {
    // 排除已存在的课程
    if (realCodes.has(plan.course_code)) return false;
    
    // 排除已通过的课程（注意：is_passed 可能是字符串）
    if (plan.is_passed === '是' || plan.is_passed === true) return false;
    if (plan.status === '通过' || plan.status === '01') return false;
    
    // 只显示未修或已选课的
    return !plan.score || ['未修读', '未修', '待选', '已选课'].includes(plan.status);
  });
};
```

---

## 6. 相关代码文件

| 文件 | 说明 |
|------|------|
| `neu_academic/report.py` | 后端原始数据解析 |
| `neu_storage/integration.py` | 字典转换、学分计算（关键！） |
| `frontend/src/pages/AcademicReportPage.js` | 培养计划页面 |
| `frontend/src/components/GPACalculator.js` | GPA模拟器导入功能 |
