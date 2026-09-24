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
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


from backend.core.auth import NEUAuthClient


logger = logging.getLogger(__name__)
CHINA_STANDARD_TIME = timezone(timedelta(hours=8), "Asia/Shanghai")


class ExperimentCourseError(RuntimeError):
    """The official experiment service failed or refused a read request."""


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

    @staticmethod
    def _parse_deadline(value: str) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("/", "-").replace("T", " ")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return (
            parsed.replace(tzinfo=CHINA_STANDARD_TIME)
            if parsed.tzinfo is None
            else parsed.astimezone(CHINA_STANDARD_TIME)
        )

    def selection_window_state(self, now: Optional[datetime] = None) -> str:
        """Return open/not_started/ended/unknown without guessing malformed dates."""
        current = now or datetime.now(CHINA_STANDARD_TIME)
        if current.tzinfo is None:
            current = current.replace(tzinfo=CHINA_STANDARD_TIME)
        else:
            current = current.astimezone(CHINA_STANDARD_TIME)
        start = self._parse_deadline(self.select_start)
        end = self._parse_deadline(self.select_end)
        if start is None or end is None:
            return "unknown"
        if current < start:
            return "not_started"
        if current > end:
            return "ended"
        return "open"
    
    @property
    def can_select(self) -> bool:
        """Whether remote metadata permits selection before local conflict checks."""
        return (
            not self.is_full
            and not self.conflict
            and self.selection_window_state() not in {"not_started", "ended"}
        )


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

    def to_dict(self) -> Dict[str, Any]:
        """Stable read model shared by the live API and the cache."""
        projects = [
            {
                "project_name": project.project_name,
                "project_code": project.project_code,
                "course_no": project.course_no,
                "must_do": project.must_do,
                "selected_round_id": project.selected_round_id,
                "select_status": project.select_status,
                "is_selected": bool(project.selected_round_id),
            }
            for project in self.projects
        ]
        return {
            "task_id": self.task_id,
            "course_name": self.course_name,
            "course_no": self.course_no,
            "credit": self.credit,
            "term_code": self.term_code,
            "experiment_hours": self.experiment_hours,
            "center_name": self.center_name,
            "college_name": self.college_name,
            "must_do_count": self.must_do_count,
            "selected_count": self.selected_count,
            "is_complete": self.is_complete,
            "projects": projects,
        }

    def selected_result_rows(self) -> List[Dict[str, Any]]:
        """Project-level results already confirmed by the official feed."""
        return [
            {
                "task_id": self.task_id,
                "course_name": self.course_name,
                "course_no": self.course_no,
                "project_name": project.project_name,
                "project_code": project.project_code,
                "selected_round_id": project.selected_round_id,
                "select_status": project.select_status,
            }
            for project in self.projects
            if project.selected_round_id
        ]


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
        except Exception as error:
            logger.warning("Experiment semester request failed")
            raise ExperimentCourseError("实验选课学期读取失败") from error

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
            if str(data.get("code")) == "0":
                courses = data.get("datas", {}).get("queryCanSelectedCourses", [])
                return [ExperimentCourse.from_dict(c) for c in courses]
            raise ExperimentCourseError("实验课程读取被教务系统拒绝")
        except ExperimentCourseError:
            raise
        except Exception as error:
            logger.warning("Experiment course request failed")
            raise ExperimentCourseError("实验课程读取失败") from error

    def get_selected_course_results(self, term_code: str) -> List[Dict[str, Any]]:
        """Read the official page-level ``选课结果`` feed.

        The experiment course list contains enough state for the course picker,
        but the official result tab uses a separate xkjg endpoint. Keeping the
        two reads separate avoids manufacturing result rows from incomplete
        project metadata.
        """
        if not term_code:
            return []
        url = f"{self.BASE_URL}/api/xkjg/queryStudentAllSelectCourseResult.do"
        try:
            response = self._client.post(
                url,
                data={"XNXQDM": term_code},
                headers=self.HEADERS,
            )
            body = response.json()
            if str(body.get("code")) != "0":
                raise ExperimentCourseError("实验选课结果读取被教务系统拒绝")
            rows = (body.get("datas") or {}).get("queryStudentAllSelectCourseResult") or []
            if not isinstance(rows, list):
                raise ExperimentCourseError("实验选课结果响应结构异常")
            result = []
            for task in rows:
                if not isinstance(task, dict):
                    continue
                task_course_name = str(task.get("courseName") or "")
                task_course_no = str(task.get("courseNo") or "")
                task_id = str(task.get("taskId") or "")
                for item in task.get("projectResultList") or []:
                    if not isinstance(item, dict):
                        continue
                    result.append({
                        "task_id": task_id,
                        "course_name": str(item.get("courseName") or task_course_name),
                        "course_no": str(item.get("courseNo") or task_course_no),
                        "project_name": str(item.get("projectName") or ""),
                        "project_code": str(item.get("projectCode") or ""),
                        "selected_round_id": str(item.get("roundId") or ""),
                        "round_name": str(item.get("roundName") or ""),
                        "select_status": str(item.get("operationType") or ""),
                        "must_do": str(item.get("isMustDo") or "") == "1",
                        "teacher": str(item.get("classTeachers") or ""),
                        "week": str(item.get("classWeeks") or ""),
                        "day": str(item.get("classDays") or ""),
                        "time": str(item.get("classSessions") or ""),
                        "location": str(item.get("classrooms") or ""),
                        "select_start": str(item.get("selectCourseStartDate") or ""),
                        "select_end": str(item.get("selectCourseEndDate") or ""),
                        "term_code": str(item.get("termCode") or term_code),
                    })
            return result
        except ExperimentCourseError:
            raise
        except Exception as error:
            logger.warning("Experiment selected-result request failed")
            raise ExperimentCourseError("实验选课结果读取失败") from error

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
            if str(data.get("code")) == "0":
                rounds = data.get("datas", {}).get("queryTaskProjectRounds", [])
                return [ExperimentRound.from_dict(r) for r in rounds]
            raise ExperimentCourseError("实验班读取被教务系统拒绝")
        except ExperimentCourseError:
            raise
        except Exception as error:
            logger.warning("Experiment round request failed")
            raise ExperimentCourseError("实验班读取失败") from error

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
