"""
NEU 实验选课 API
================

提供实验选课相关功能：
- 查询可选课程
- 查询实验班列表
- 选课/退课
- 获取当前学年学期
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


from backend.core.auth import NEUAuthClient


logger = logging.getLogger(__name__)


@dataclass
class ExperimentProject:
    """实验项目"""
    project_name: str
    project_code: str           # SYXMDM
    course_no: str              # KCH
    must_do: bool
    selected_round_id: str      # PKLC_WID (已选实验班ID)
    select_status: str

    @classmethod
    def from_dict(cls, data: Dict) -> "ExperimentProject":
        return cls(
            project_name=data.get("projectName", ""),
            project_code=data.get("projectCode", ""),
            course_no=data.get("courseNo", ""),
            must_do=data.get("mustDo", False),
            selected_round_id=data.get("selectedRoundId", ""),
            select_status=data.get("selectStatus", ""),
        )


@dataclass
class ExperimentRound:
    """实验班"""
    wid: str                    # PKLC_WID (实验班ID)
    round_name: str
    teacher: str
    selected_count: int
    capacity: int
    week: str
    day: str
    time: str
    location: str
    select_start: str
    select_end: str
    conflict: bool = False
    selected: bool = False

    @classmethod
    def from_dict(cls, data: Dict) -> "ExperimentRound":
        return cls(
            wid=data.get("wid", ""),
            round_name=data.get("roundName", ""),
            teacher=data.get("classTeachers", ""),
            selected_count=data.get("selectedNums", 0),
            capacity=data.get("courseCapacity", 0),
            week=data.get("classWeeks", ""),
            day=data.get("classDays", ""),
            time=data.get("classSessions", ""),
            location=data.get("classrooms", "") or "",
            select_start=data.get("selectCourseStartDate", ""),
            select_end=data.get("selectCourseEndDate", ""),
            conflict=data.get("conflict", False),
            selected=data.get("selected", False),
        )
    
    @property
    def is_full(self) -> bool:
        """是否已满"""
        return self.selected_count >= self.capacity
    
    @property
    def can_select(self) -> bool:
        """是否可选（未满且无冲突）"""
        return not self.is_full and not self.conflict


@dataclass
class ExperimentCourse:
    """实验课程"""
    task_id: str                # SYRW_WID
    course_name: str
    course_no: str              # KCH
    credit: float
    term_code: str              # XNXQDM
    experiment_hours: float
    center_name: str            # 实验中心名称
    college_name: str           # 开课学院
    must_do_count: int
    projects: List[ExperimentProject] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict) -> "ExperimentCourse":
        projects = [ExperimentProject.from_dict(p) for p in data.get("allExperimentProjectList", [])]
        return cls(
            task_id=data.get("taskId", ""),
            course_name=data.get("courseName", ""),
            course_no=data.get("courseNo", ""),
            credit=data.get("credit", 0.0),
            term_code=data.get("termCode", ""),
            experiment_hours=data.get("experimentalHours", 0.0),
            center_name=data.get("experimentCenterName", ""),
            college_name=data.get("openingCollegeName", ""),
            must_do_count=data.get("mustDoProjectNums", 0),
            projects=projects,
        )

    def get_unselected(self) -> List[ExperimentProject]:
        """获取未选项目"""
        return [p for p in self.projects if not p.selected_round_id]

    def get_selected(self) -> List[ExperimentProject]:
        """获取已选项目"""
        return [p for p in self.projects if p.selected_round_id]
    
    @property
    def selected_count(self) -> int:
        """已选项目数"""
        return len(self.get_selected())
    
    @property
    def is_complete(self) -> bool:
        """是否已完成所有必做项目"""
        must_do = self.must_do_count or 0
        return self.selected_count >= must_do


class ExperimentCourseAPI:
    """实验选课 API"""

    # 注意：教务系统可能使用 HTTP 或 HTTPS，协议回退由 NEUAuthClient 自动处理
    BASE_URL = "https://jwxt.neu.edu.cn/jwapp/sys/syxkapp"
    HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

    def __init__(self, client: NEUAuthClient):
        self._client = client

    def get_semester(self) -> Optional[str]:
        """
        获取当前学年学期
        
        Returns:
            学年学期代码，如 "2025-2026-2"
        """
        url = f"{self.BASE_URL}/api/sypz/queryAcademicYearSemester.do"
        try:
            resp = self._client.post(url, data={}, headers=self.HEADERS)
            return resp.json().get("datas", {}).get("queryAcademicYearSemester")
        except Exception:
            logger.warning("Experiment semester request failed")
            return None

    def get_courses(self, term_code: str = None) -> List[ExperimentCourse]:
        """
        获取可选课程列表
        
        Args:
            term_code: 学年学期代码，不传则自动获取当前学期
            
        Returns:
            实验课程列表
        """
        if not term_code:
            term_code = self.get_semester()
        if not term_code:
            return []
        
        url = f"{self.BASE_URL}/api/xsxk/queryCanSelectedCourses.do"
        try:
            resp = self._client.post(url, data={"XNXQDM": term_code}, headers=self.HEADERS)
            data = resp.json()
            if data.get("code") == "0":
                courses = data.get("datas", {}).get("queryCanSelectedCourses", [])
                return [ExperimentCourse.from_dict(c) for c in courses]
        except Exception:
            logger.warning("Experiment course request failed")
        return []

    def get_rounds(self, term_code: str, task_id: str, course_no: str, project_code: str) -> List[ExperimentRound]:
        """
        获取实验班列表
        
        Args:
            term_code: 学年学期代码
            task_id: 任务ID (SYRW_WID)
            course_no: 课程号 (KCH)
            project_code: 实验项目代码 (SYXMDM)
            
        Returns:
            实验班列表
        """
        url = f"{self.BASE_URL}/api/xsxk/queryTaskProjectRounds.do"
        try:
            resp = self._client.post(url, data={
                "XNXQDM": term_code,
                "RWID": task_id,
                "KCH": course_no,
                "SYXMDM": project_code,
                "SYRW_WID": task_id,
            }, headers=self.HEADERS)
            data = resp.json()
            if data.get("code") == "0":
                rounds = data.get("datas", {}).get("queryTaskProjectRounds", [])
                return [ExperimentRound.from_dict(r) for r in rounds]
        except Exception:
            logger.warning("Experiment round request failed")
        return []

    def select(self, term_code: str, task_id: str, project_code: str, round_id: str) -> Dict:
        """
        选课
        
        Args:
            term_code: 学年学期代码
            task_id: 任务ID (SYRW_WID)
            project_code: 实验项目代码 (SYXMDM)
            round_id: 实验班ID (PKLC_WID)
            
        Returns:
            操作结果 {"code": "0", "msg": "..."}
        """
        url = f"{self.BASE_URL}/api/xsxk/selectedMustDoProjectRound.do"
        try:
            resp = self._client.post(url, data={
                "XNXQDM": term_code,
                "SYRW_WID": task_id,
                "SYXMDM": project_code,
                "PKLC_WID": round_id,
            }, headers=self.HEADERS)
            return resp.json()
        except Exception:
            logger.warning("Experiment selection request failed")
            return {"code": "-1", "msg": "远端选课请求失败"}

    def deselect(self, term_code: str, task_id: str, project_code: str, round_id: str) -> Dict:
        """
        退课
        
        Args:
            term_code: 学年学期代码
            task_id: 任务ID (SYRW_WID)
            project_code: 实验项目代码 (SYXMDM)
            round_id: 实验班ID (PKLC_WID)
            
        Returns:
            操作结果 {"code": "0", "msg": "..."}
        """
        url = f"{self.BASE_URL}/api/xsxk/deselectedMustDoProjectRound.do"
        try:
            resp = self._client.post(url, data={
                "XNXQDM": term_code,
                "SYRW_WID": task_id,
                "SYXMDM": project_code,
                "PKLC_WID": round_id,
            }, headers=self.HEADERS)
            return resp.json()
        except Exception:
            logger.warning("Experiment deselection request failed")
            return {"code": "-1", "msg": "远端退课请求失败"}
