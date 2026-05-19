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
    required_credits: float = 0.0            # 要求学分 (creditsRequired)
    earned_credits: float = 0.0              # 已获得学分 (creditsEarned)
    taken_credits: float = 0.0               # 已选学分 (creditsTaken)
    selection_credits: float = 0.0           # 待选学分 (creditsSelection)
    is_passed: bool = False                  # 是否满足要求 (passRequired)
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
    
    def get_report(self) -> Optional[AcademicReport]:
        """
        获取学业监测报告
        
        Returns:
            AcademicReport 对象，失败返回 None
        """
        try:
            resp = self._client.post(self.API_URL, data={}, headers=self.HEADERS)
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
        report.program_name = fanbx.get("educationalProgramName", "")
        
        # 注意：fanbx 本身可能没有总学分字段，需要从子类别计算
        report.total_required = float(fanbx.get("creditsRequired") or 0)
        report.total_earned = float(fanbx.get("creditsEarned") or 0)
        report.total_taken = float(fanbx.get("creditsTaken") or 0)
        report.credits_outside = float(fanbx.get("creditsOutOfProgram") or 0)
        
        # 解析学生信息（从第一个课程数据中获取）
        children = fanbx.get("children", [])
        if children and len(children) > 0:
            def find_first_course(node):
                if "data" in node and node["data"] and len(node["data"]) > 0:
                    return node["data"][0]
                for child in node.get("children", []) or []:
                    result = find_first_course(child)
                    if result:
                        return result
                return None
            
            first_course = find_first_course(children[0])
            if first_course:
                report.student_id = first_course.get("XH", "")
                report.student_name = first_course.get("XM", "")
        
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
        outside_courses_data = fanbx.get("outsideProgramCourses", [])
        report.outside_courses = self._parse_outside_courses(outside_courses_data)
        
        return report
    
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
        
        cat.required_credits = float(cat_data.get("creditsRequired") or 0)
        cat.earned_credits = float(cat_data.get("creditsEarned") or 0)
        cat.taken_credits = float(cat_data.get("creditsTaken") or 0)
        cat.selection_credits = float(cat_data.get("creditsSelection") or 0)
        cat.is_passed = cat_data.get("passRequired", False)
        
        # 判断课程性质：检查当前节点名称是否包含"必修"/"选修"
        nature_hint = parent_nature_hint
        if "必修" in cat.name:
            nature_hint = "必修"
        elif "选修" in cat.name:
            nature_hint = "选修"
        # 特殊处理：通识选修类下的课程默认为选修
        elif "通识选修" in cat.name:
            nature_hint = "选修"
        
        # 解析子类别，传递 nature_hint
        children = cat_data.get("children") or []
        if children:
            cat.children = self._parse_categories(children, nature_hint)
        
        # 解析课程列表（从 checkCourseVOS 字段，这是关键！）
        courses_data = cat_data.get("checkCourseVOS") or []
        if courses_data:
            cat.courses = self._parse_courses_from_check(courses_data, cat.name, nature_hint)
        
        return cat
    
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
        """解析方案外课程"""
        courses = []
        for course_data in courses_data:
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
