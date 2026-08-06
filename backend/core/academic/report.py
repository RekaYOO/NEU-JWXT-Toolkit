"""
neu_academic/report.py
======================
学业监测报告 API - 挂载到 NEUAuthClient

提供个人学业监测报告（培养计划）的获取和导出功能
"""

import csv
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class CourseInfo:
    """课程信息"""
    # 基本信息
    course_name: str = ""                    # 课程名称 (KCM)
    course_code: str = ""                    # 课程代码 (KCH)
    course_category: str = ""                # 课程类别
    course_subcategory: str = ""             # 课程子类别
    course_nature: str = ""                  # 课程性质 (KCXZDM_DISPLAY)
    
    # 学分信息
    credit: float = 0.0                      # 学分 (XF)
    required_credit: float = 0.0             # 要求学分
    earned_credit: float = 0.0               # 已获得学分
    
    # 成绩信息
    score: str = ""                          # 成绩 (XSZCJ)
    is_passed: str = ""                      # 是否通过 (SFJG_DISPLAY)
    status: str = ""                         # 状态 (ZT_DISPLAY)
    
    # 学期信息
    select_term: str = ""                    # 选课学年学期 (XKXNXQDM_DISPLAY)
    score_term: str = ""                     # 成绩学年学期 (CJXNXQDM_DISPLAY)
    plan_term: str = ""                      # 计划学年学期 (JHXNXQDM_DISPLAY)
    
    # 其他信息
    exam_type: str = ""                      # 考核方式 (KSLXDM_DISPLAY)
    retake_status: str = ""                  # 重修重考状态 (CXCKDM_DISPLAY)
    substitute_course: str = ""              # 替代课程名 (TDKCM)
    substitute_credit: str = ""              # 替代课程学分 (TDKCXF)
    department: str = ""                     # 开课单位 (KKDWDM_DISPLAY)
    
    # 原始数据
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CategoryInfo:
    """类别信息（通识类、学科基础类等）"""
    name: str = ""                           # 类别名称 (courseCategoryName)
    category_code: str = ""                  # 类别代码 (courseCategory)
    source_id: str = ""                      # 接口节点 ID
    course_group_id: str = ""                # 课程组 ID
    course_group_wid: str = ""               # 课程组稳定 ID
    requirement_type: str = "unknown"        # required/elective/mixed/unknown
    required_credits: float = 0.0            # 要求学分 (creditsRequired)
    declared_required_credits: float = 0.0   # 接口原始要求学分
    requirement_adjustment: float = 0.0      # 兼容旧缓存；不再调整接口要求学分
    requires_child_minimums_and_total: bool = False  # 子类最低要求与父类总量双重约束
    earned_credits: float = 0.0              # 已获得学分 (creditsEarned)
    taken_credits: float = 0.0               # 已选学分 (creditsTaken)
    selection_credits: float = 0.0           # 待选学分 (creditsSelection)
    is_passed: bool = False                  # 是否满足要求 (passed)
    reported_passed: Optional[bool] = None   # 接口是否明确给出综合完成状态
    pass_required: bool = False              # 课程组是否要求通过 (passRequired)
    course_count_required: int = 0           # 要求课程数
    course_count_taken: int = 0              # 已修课程数
    group_count_required: int = 0            # 要求子组数
    group_count_taken: int = 0               # 已满足子组数
    credits_group_judgement: float = 0.0      # 课程组学分判定值
    children: List[Any] = field(default_factory=list)  # 子类别
    courses: List[CourseInfo] = field(default_factory=list)  # 课程列表
    
    @property
    def remaining_credits(self) -> float:
        """还差多少学分（要求学分 - 已通过学分 - 已选学分）"""
        return max(0, self.required_credits - self.earned_credits - self.taken_credits)
    
    @property
    def total_earned_credits(self) -> float:
        """已修总学分（已通过 + 已选）"""
        return self.earned_credits + self.taken_credits


@dataclass
class AcademicReport:
    """学业监测报告"""
    # 基本信息
    student_name: str = ""                   # 姓名
    student_id: str = ""                     # 学号 (XH)
    grade: str = ""                          # 年级
    college: str = ""                        # 学院
    major: str = ""                          # 专业
    class_name: str = ""                     # 班级
    expected_graduation: str = ""            # 预计毕业日期
    
    # 培养方案信息
    program_code: str = ""                   # 培养方案代码 (educationalProgramCode)
    program_name: str = ""                   # 培养方案名称
    total_required: float = 0.0              # 总要求学分 (creditsRequired)
    total_earned: float = 0.0                # 总已获得学分 (creditsEarned)
    total_taken: float = 0.0                 # 总已选学分 (creditsTaken)
    credits_outside: float = 0.0             # 方案外学分 (creditsOutOfProgram)
    
    # 计算时间
    calculated_time: str = ""                # 计算时间 (calculatedTime)
    
    # 类别列表
    categories: List[CategoryInfo] = field(default_factory=list)
    outside_courses: List[CourseInfo] = field(default_factory=list)
    
    # 原始数据
    raw_data: Dict[str, Any] = field(default_factory=dict)


class AcademicReportAPI:
    """
    学业监测报告 API
    
    通过 NEUAuthClient.academic_report 访问
    """
    
    API_URL = "https://jwxt.neu.edu.cn/jwapp/sys/byshapp/api/grbg/queryXyzhbx.do"

    # 学业监测页的默认查询上下文。培养方案代码 PYFADM 属于账号相关
    # 的变量，不能在这里硬编码；服务端会按当前登录学生选择默认方案。
    DEFAULT_QUERY_DATA = {
        "fromPage": "grxyjcbg",
        "SCLX": "04",
        "XDLX": "01",
    }
    
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://jwxt.neu.edu.cn/jwapp/sys/byshapp/*default/index.do",
    }
    
    def __init__(self, auth_client):
        """
        初始化
        
        Args:
            auth_client: NEUAuthClient 实例
        """
        self._client = auth_client
    
    def get_report(self, program_code: Optional[str] = None) -> Optional[AcademicReport]:
        """
        获取学业监测报告

        Args:
            program_code: 可选培养方案代码（PYFADM）。不传时由教务系统
                根据当前登录学生选择默认方案。
        
        Returns:
            AcademicReport 对象，失败返回 None
        """
        try:
            query_data = self.DEFAULT_QUERY_DATA.copy()
            if program_code:
                query_data["PYFADM"] = str(program_code)
            resp = self._client.post(
                self.API_URL,
                data=query_data,
                headers=self.HEADERS,
            )
            data = resp.json()
            
            if data.get("code") != "0":
                return None
            
            return self._parse_report(data["datas"]["queryXyzhbx"])
        except Exception as e:
            return None
    
    def _parse_report(self, data: Dict[str, Any]) -> AcademicReport:
        """解析学业监测报告"""
        report = AcademicReport()
        report.raw_data = data
        
        # 解析基本信息
        report.calculated_time = data.get("calculatedTime", "")
        
        # 解析培养方案信息
        fanbx = data.get("fanbx", {})
        report.program_code = fanbx.get("educationalProgramCode", "")
        # 2024 级及以前通常使用 educationalProgramName；2025 级方案
        # 将显示名称放在 fanbx.name（例如“2025 机器人工程”）。
        report.program_name = (
            fanbx.get("educationalProgramName")
            or fanbx.get("name")
            or ""
        )
        
        # 注意：fanbx 本身可能没有总学分字段，需要从子类别计算
        report.total_required = float(fanbx.get("creditsRequired") or 0)
        report.total_earned = float(fanbx.get("creditsEarned") or 0)
        report.total_taken = float(fanbx.get("creditsTaken") or 0)
        report.credits_outside = float(fanbx.get("creditsOutOfProgram") or 0)
        
        # 解析学生信息（从第一个课程数据中获取）
        children = fanbx.get("children", [])
        if children:
            first_course = self._find_first_course(children)
            if first_course:
                report.student_id = (
                    first_course.get("XH")
                    or first_course.get("studentId")
                    or ""
                )
                report.student_name = (
                    first_course.get("XM")
                    or first_course.get("studentName")
                    or ""
                )
        if not report.student_id:
            report.student_id = str(getattr(self._client, "username", "") or "")
        if report.student_id[:4].isdigit():
            report.grade = report.student_id[:4]
        
        # 解析类别
        report.categories = self._parse_categories(children)
        
        # 如果 fanbx 没有总学分字段，从子类别计算
        if report.total_required == 0 and report.categories:
            report.total_required = sum(cat.required_credits for cat in report.categories)
        if report.total_earned == 0 and report.categories:
            report.total_earned = sum(cat.earned_credits for cat in report.categories)
        if report.total_taken == 0 and report.categories:
            report.total_taken = sum(cat.taken_credits for cat in report.categories)
        
        # 解析方案外课程
        # 旧版把方案外课程放在 fanbx.outsideProgramCourses；2025 级响应
        # 改为 queryXyzhbx.fawbx（新字段）及 fawbxMap（旧式字段映射）。
        outside_courses_data = fanbx.get("outsideProgramCourses") or []
        if not outside_courses_data:
            outside_courses_data = data.get("fawbx") or data.get("fawbxMap") or []
        report.outside_courses = self._parse_outside_courses(outside_courses_data)
        
        return report

    def _find_first_course(self, nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """在任意深度的类别树中查找第一条课程记录。"""
        for node in nodes:
            legacy_courses = node.get("data") or []
            if legacy_courses:
                return legacy_courses[0]

            checked_courses = node.get("checkCourseVOS") or []
            if checked_courses:
                return checked_courses[0]

            first_course = self._find_first_course(node.get("children") or [])
            if first_course:
                return first_course
        return None
    
    def _parse_categories(self, categories_data: List[Dict], parent_nature_hint: str = "") -> List[CategoryInfo]:
        """解析类别列表"""
        categories = []
        for cat_data in categories_data:
            cat = self._parse_category(cat_data, parent_nature_hint)
            if cat:
                categories.append(cat)
        return categories
    
    def _parse_category(self, cat_data: Dict, parent_nature_hint: str = "") -> Optional[CategoryInfo]:
        """解析单个类别"""
        cat = CategoryInfo()
        
        # 关键：name 字段通常包含 "必修"、"选修" 等具体名称
        # 如果 name 和 courseCategoryName 不同，使用 name 作为显示名称
        name_field = cat_data.get("name", "")
        category_name_field = cat_data.get("courseCategoryName", "")
        
        # 优先使用 name 字段，但如果为空或与 categoryName 相同，则使用 categoryName
        if name_field and name_field != category_name_field:
            cat.name = name_field
        else:
            cat.name = category_name_field or name_field or "未命名"

        cat.category_code = str(cat_data.get("courseCategory") or "")
        cat.source_id = str(cat_data.get("id") or "")
        cat.course_group_id = str(cat_data.get("courseGroupId") or "")
        cat.course_group_wid = str(cat_data.get("courseGroupWid") or "")
        cat.required_credits = float(cat_data.get("creditsRequired") or 0)
        cat.declared_required_credits = cat.required_credits
        cat.earned_credits = float(cat_data.get("creditsEarned") or 0)
        cat.taken_credits = float(cat_data.get("creditsTaken") or 0)
        cat.selection_credits = float(cat_data.get("creditsSelection") or 0)
        reported_passed = cat_data.get("passed")
        cat.reported_passed = (
            reported_passed if isinstance(reported_passed, bool) else None
        )
        cat.is_passed = bool(reported_passed)
        cat.pass_required = bool(cat_data.get("passRequired", False))
        cat.course_count_required = int(cat_data.get("courseCountRequired") or 0)
        cat.course_count_taken = int(cat_data.get("courseCountTaken") or 0)
        cat.group_count_required = int(cat_data.get("groupCountRequired") or 0)
        cat.group_count_taken = int(cat_data.get("groupCountTaken") or 0)
        cat.credits_group_judgement = float(
            cat_data.get("creditsCourseGroupJudgement") or 0
        )

        # 课程性质代码比展示名称稳定；没有课程的规则节点再继承父级语义，
        # 最后才使用中文名称作为旧响应兼容回退。
        cat.requirement_type = self._infer_requirement_type(
            cat_data,
            parent_nature_hint,
        )
        nature_hint = {
            "required": "必修",
            "elective": "选修",
        }.get(cat.requirement_type, parent_nature_hint)
        
        # 解析子类别，传递 nature_hint
        children = cat_data.get("children") or []
        if children:
            cat.children = self._parse_categories(children, nature_hint)
            self._mark_unallocated_elective_requirement(cat)
        
        # 解析课程列表（从 checkCourseVOS 字段，这是关键！）
        courses_data = cat_data.get("checkCourseVOS") or []
        if courses_data:
            cat.courses = self._parse_courses_from_check(courses_data, cat.name, nature_hint)
        
        return cat

    @staticmethod
    def _mark_unallocated_elective_requirement(cat: CategoryInfo) -> None:
        """
        标记“子类最低要求 + 父类总学分”双重约束的选修父组。

        部分培养方案中，选修父组要求学分大于直接子组要求之和。这一
        差额不是某个子类的额外要求：每个子类仍只需满足接口声明的
        最低学分，同时所有子类的实际修读学分之和还需达到父组要求。
        """
        if cat.requirement_type != "elective" or not cat.children:
            return

        declared_total = sum(
            child.declared_required_credits for child in cat.children
        )
        cat.requires_child_minimums_and_total = (
            cat.required_credits > declared_total + 1e-9
        )

    def _infer_requirement_type(
        self,
        cat_data: Dict[str, Any],
        parent_nature_hint: str = "",
    ) -> str:
        """根据课程代码、继承规则和显示名推断类别的修读类型。"""
        nature_codes = set()
        for course in cat_data.get("checkCourseVOS") or []:
            if course.get("courseNature"):
                nature_codes.add(str(course["courseNature"]))
        for course in cat_data.get("data") or []:
            if course.get("KCXZDM"):
                nature_codes.add(str(course["KCXZDM"]))

        mapped_types = set()
        if "01" in nature_codes:
            mapped_types.add("required")
        if "02" in nature_codes:
            mapped_types.add("elective")
        if len(mapped_types) == 1:
            return next(iter(mapped_types))
        if len(mapped_types) > 1:
            return "mixed"

        name = str(
            cat_data.get("name")
            or cat_data.get("courseCategoryName")
            or ""
        )
        if "选修" in name:
            return "elective"
        if "必修" in name:
            return "required"
        if parent_nature_hint == "选修":
            return "elective"
        if parent_nature_hint == "必修":
            return "required"
        return "unknown"
    
    def _parse_courses(self, courses_data: List[Dict], category_name: str = "", subcategory_name: str = "") -> List[CourseInfo]:
        """解析课程列表（从 data 字段）"""
        courses = []
        for course_data in courses_data:
            course = self._parse_course(course_data)
            if course:
                course.course_category = category_name
                course.course_subcategory = subcategory_name
                courses.append(course)
        return courses
    
    def _parse_courses_from_check(self, courses_data: List[Dict], category_name: str = "", nature_hint: str = "") -> List[CourseInfo]:
        """解析课程列表（从 checkCourseVOS 字段，这是主要的课程来源）"""
        courses = []
        for course_data in courses_data:
            course = self._parse_course_from_check(course_data, category_name, nature_hint)
            if course:
                course.course_category = category_name
                courses.append(course)
        return courses
    
    def _parse_course(self, course_data: Dict) -> Optional[CourseInfo]:
        """解析单个课程（从 data 字段，使用 KCM/KCH 等字段）"""
        course = CourseInfo()
        course.raw_data = course_data
        
        # 基本信息
        course.course_name = course_data.get("KCM", "")
        course.course_code = course_data.get("KCH", "")
        course.course_nature = course_data.get("KCXZDM_DISPLAY", "")
        
        # 学分信息
        course.credit = float(course_data.get("XF") or 0)
        
        # 成绩信息
        course.score = str(course_data.get("XSZCJ") or "")
        course.is_passed = course_data.get("SFJG_DISPLAY", "否")
        course.status = course_data.get("ZT_DISPLAY", "")
        
        # 学期信息
        course.select_term = course_data.get("XKXNXQDM_DISPLAY", "")
        course.score_term = course_data.get("CJXNXQDM_DISPLAY", "")
        course.plan_term = course_data.get("JHXNXQDM_DISPLAY", "")
        
        # 其他信息
        course.exam_type = course_data.get("KSLXDM_DISPLAY", "")
        course.retake_status = course_data.get("CXCKDM_DISPLAY", "")
        course.substitute_course = course_data.get("TDKCM", "")
        course.substitute_credit = str(course_data.get("TDKCXF") or "")
        course.department = course_data.get("KKDWDM_DISPLAY", "")
        
        return course
    
    def _parse_course_from_check(self, course_data: Dict, category_name: str = "", nature_hint: str = "") -> Optional[CourseInfo]:
        """解析单个课程（从 checkCourseVOS 字段，使用 courseName/courseId 等字段）"""
        course = CourseInfo()
        course.raw_data = course_data
        
        # 基本信息（字段名和 data 中的不同）
        course.course_name = course_data.get("courseName", "")
        course.course_code = course_data.get("courseId", "")
        
        # 课程性质代码转显示文本
        nature_code = course_data.get("courseNature", "")
        if nature_code == "01":
            course.course_nature = "必修"
        elif nature_code == "02":
            course.course_nature = "选修"
        elif nature_code:
            course.course_nature = nature_code
        elif nature_hint:
            # 如果 courseNature 为空，使用父节点传递的 hint
            course.course_nature = nature_hint
        else:
            course.course_nature = ""
        
        # 学分信息
        course.credit = float(course_data.get("credit") or 0)
        
        # 成绩信息
        course.score = str(course_data.get("scoreView") or course_data.get("point") or "")
        # passed 字段是布尔值，表示是否通过
        passed = course_data.get("passed", False)
        course.is_passed = "是" if passed else "否"
        
        # 状态转换
        # 状态码说明：01=通过, 02=不通过/挂科, 03=已选课, 04=未修读
        status_code = course_data.get("status", "")
        if status_code == "01":
            course.status = "通过"
        elif status_code == "02":
            course.status = "挂科"  # 预留：不通过/挂科状态
        elif status_code == "03":
            course.status = "已选课"
        elif status_code == "04":
            course.status = "未修读"
        else:
            course.status = status_code
        
        # 学期信息
        course.select_term = course_data.get("courseSelectionSchoolYearTermCode", "")
        course.score_term = course_data.get("pointSchoolYearTermCode", "")
        course.plan_term = course_data.get("schoolYearTerms", "")
        
        # 其他信息
        exam_type = course_data.get("examType", "")
        if exam_type == "01":
            course.exam_type = "考试"
        elif exam_type == "02":
            course.exam_type = "考查"
        else:
            course.exam_type = exam_type
        
        retake_type = course_data.get("retakeType", "")
        if retake_type == "01":
            course.retake_status = "初修"
        elif retake_type == "02":
            course.retake_status = "重修"
        else:
            course.retake_status = retake_type
        
        course.substitute_course = course_data.get("replacedCourseName", "")
        course.substitute_credit = str(course_data.get("replacedCourseCredit") or "")
        course.department = course_data.get("courseDept", "")
        
        return course
    
    def _parse_outside_courses(self, courses_data: List[Dict]) -> List[CourseInfo]:
        """解析方案外课程，兼容旧式字段和 2025 级 fawbx 字段。"""
        courses = []
        for course_data in courses_data:
            if course_data.get("courseName") is not None or course_data.get("courseId") is not None:
                course = self._parse_course_from_check(course_data)
                if course:
                    course.course_category = "方案外课程"
                    courses.append(course)
                continue

            course = CourseInfo()
            course.raw_data = course_data
            
            course.course_name = course_data.get("KCM", "")
            course.course_code = course_data.get("KCH", "")
            course.credit = float(course_data.get("XF", 0) or 0)
            course.score = str(course_data.get("XSZCJ", "") or "")
            course.is_passed = course_data.get("SFJG_DISPLAY", "否")
            course.select_term = course_data.get("XKXNXQDM_DISPLAY", "")
            course.score_term = course_data.get("CJXNXQDM_DISPLAY", "")
            course.course_category = course_data.get("KCLBDM_DISPLAY", "")
            course.course_nature = course_data.get("KCXZDM_DISPLAY", "")
            course.retake_status = course_data.get("CXCKDM_DISPLAY", "")
            course.department = course_data.get("KKDWDM_DISPLAY", "")
            
            courses.append(course)
        return courses
    
    def export_to_csv(self, report: AcademicReport, output_dir: str = "./data") -> Dict[str, str]:
        """
        导出学业监测报告为CSV文件
        
        Args:
            report: AcademicReport 对象
            output_dir: 输出目录
            
        Returns:
            导出的文件路径字典
        """
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        files = {}
        
        # 1. 导出基本信息
        basic_info = [{
            "姓名": report.student_name,
            "学号": report.student_id,
            "年级": report.grade,
            "学院": report.college,
            "专业": report.major,
            "班级": report.class_name,
            "预计毕业日期": report.expected_graduation,
            "培养方案代码": report.program_code,
            "培养方案名称": report.program_name,
            "总要求学分": report.total_required,
            "总已获得学分": report.total_earned,
            "总已选学分": report.total_taken,
            "方案外学分": report.credits_outside,
            "计算时间": report.calculated_time,
        }]
        
        basic_file = os.path.join(output_dir, f"academic_report_basic_{timestamp}.csv")
        with open(basic_file, "w", newline="", encoding="utf-8-sig") as f:
            if basic_info:
                writer = csv.DictWriter(f, fieldnames=basic_info[0].keys())
                writer.writeheader()
                writer.writerows(basic_info)
        files["basic"] = basic_file
        
        # 2. 导出类别统计
        categories_flat = self._flatten_categories(report.categories)
        if categories_flat:
            categories_file = os.path.join(output_dir, f"academic_report_categories_{timestamp}.csv")
            with open(categories_file, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=categories_flat[0].keys())
                writer.writeheader()
                writer.writerows(categories_flat)
            files["categories"] = categories_file
        
        # 3. 导出课程列表
        courses_flat = self._flatten_courses(report.categories)
        if courses_flat:
            courses_file = os.path.join(output_dir, f"academic_report_courses_{timestamp}.csv")
            with open(courses_file, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=courses_flat[0].keys())
                writer.writeheader()
                writer.writerows(courses_flat)
            files["courses"] = courses_file
        
        # 4. 导出方案外课程
        if report.outside_courses:
            outside_data = []
            for course in report.outside_courses:
                outside_data.append({
                    "课程名称": course.course_name,
                    "课程代码": course.course_code,
                    "学分": course.credit,
                    "成绩": course.score,
                    "是否通过": course.is_passed,
                    "选课学期": course.select_term,
                    "成绩学期": course.score_term,
                    "课程类别": course.course_category,
                    "课程性质": course.course_nature,
                    "重修重考": course.retake_status,
                    "开课单位": course.department,
                })
            
            outside_file = os.path.join(output_dir, f"academic_report_outside_{timestamp}.csv")
            with open(outside_file, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=outside_data[0].keys())
                writer.writeheader()
                writer.writerows(outside_data)
            files["outside"] = outside_file
        
        # 5. 导出完整JSON
        json_file = os.path.join(output_dir, f"academic_report_full_{timestamp}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(report.raw_data, f, ensure_ascii=False, indent=2)
        files["json"] = json_file
        
        return files
    
    def _flatten_categories(self, categories: List[CategoryInfo], parent_name: str = "") -> List[Dict]:
        """将类别层次结构扁平化为列表"""
        result = []
        for cat in categories:
            cat_info = {
                "类别名称": cat.name,
                "父类别": parent_name,
                "要求学分": cat.required_credits,
                "已获得学分": cat.earned_credits,
                "已选学分": cat.taken_credits,
                "待选学分": cat.selection_credits,
                "是否满足": "是" if cat.is_passed else "否",
            }
            result.append(cat_info)
            
            if cat.children:
                result.extend(self._flatten_categories(cat.children, cat.name))
        return result
    
    def _flatten_courses(self, categories: List[CategoryInfo], parent_category: str = "") -> List[Dict]:
        """将所有类别的课程扁平化为列表"""
        result = []
        for cat in categories:
            current_category = parent_category if parent_category else cat.name
            
            for course in cat.courses:
                course_info = {
                    "课程名称": course.course_name,
                    "课程代码": course.course_code,
                    "主类别": current_category,
                    "子类别": cat.name if cat.name != current_category else "",
                    "课程性质": course.course_nature,
                    "学分": course.credit,
                    "成绩": course.score,
                    "是否通过": course.is_passed,
                    "修读状态": course.status,
                    "选课学期": course.select_term,
                    "成绩学期": course.score_term,
                    "计划学期": course.plan_term,
                    "考核方式": course.exam_type,
                    "重修重考": course.retake_status,
                    "替代课程": course.substitute_course,
                    "替代学分": course.substitute_credit,
                    "开课单位": course.department,
                }
                result.append(course_info)
            
            if cat.children:
                result.extend(self._flatten_courses(cat.children, current_category))
        return result
