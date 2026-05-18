"""
NEU 教学质量评价系统 API（zljk.neu.edu.cn）
============================================

提供评教相关功能：
- 获取学生评教任务列表（一级页面）
- 获取任务下的课程评教列表（二级页面）
- 获取评教指标体系（评分细则）
- 构建评分数据（自动评分策略）
- 提交评教结果

评教系统入口: http://zljk.neu.edu.cn/evaluate/studentEvaluate/student-jdp/index
API 基路径: http://zljk.neu.edu.cn/api

认证方式：JWT（与 jwxt.neu.edu.cn 的 session 认证完全不同）
  1. CAS 登录获取 service ticket
  2. GET /api/cas_ticket_verify?ticket=xxx 换取 JWT accessToken
  3. 后续请求带 Authorization: Bearer {jwt} + X-Tenant-ID: default

API 端点：
  学期周期：
    POST /api/qsmart/datacenter/TSysCycleController/tSysCycle/findCycle
    Body: null
    Response: [{cycleid, value("2025-2026-2"), isdefault(1=当前默认)}]

  一级页面（任务列表）：
    POST /api/TSysDdJxapController/tSysDdJxap/findJdpXqByTaskidHZ
    Body: {"page": {...}, "xnxq": "2025-2026-2"}
    Response: {"rowCount": N, "items": [{taskid, taskname, count, ypcount, wpcount, ...}]}

  二级页面（课程列表）：
    POST /api/TSysDdJxapController/tSysDdJxap/findJdpXqByTaskid
    Body: {"page": {...}, "kcmc": "", "jgxm": "", "xnxq": "...", "taskid": "..."}
    Response: {"rowCount": N, "items": [{xspjid, kcmc, jgxm, issubmit, ...}]}

  查询已有评教信息（用于判断已评/未评）：
    POST /api/TSysDdJxapController/findPjDxqk
    Body: {"taskid": "..."}
    Response: [{xspjid, jgxm, kcmc, issave, ...}]

  获取评教指标体系：
    GET /api/TSysDdEvalTargetController/queryEvalTargetTreeForEval
         ?libid=...&tkid=&taskid=...
    Response: {sfkqzbqz, preface, zbkzf, list: [{zbid, zbmc, evaltype, sfdx, sfbt, weight, fz, leveljson, ...}]}

  获取评教指标库：
    POST /api/TSysDdKbxxController/getTasklib/{taskid}
    Response: [{id, libname, score}]

  查询评教开关信息：
    POST /api/TSysDdJxapBcpController/queryKzInfo
    Body: {"taskid": "..."}
    Response: {dffs, ismydtz, sfkqpjsjkz, gffs, sfkqdgfkz, ...}

  提交评教结果：
    POST /api/TSysDdEvalResultJdpController/tSysDdEvalResultJdp/saveJdp
    Body: {resultList: [...], task: {...}}
    Response: "success"

评分规则：
- 打分等级: 6=100分, 5=90分, 4=80分, 3=70分, 2=60分, 1=50分
- evaltype: 1=选择型, sfdx: 0=单选, 1=多选
- 各指标 weight=10%（共10项），总计1000分（zbkzf=1000.0）
- 全部相同分数会被拦截 -> 首项打5其余打6 或 首项打2其余打1
- 提交前有敏感词检测

"""

import sys
import os
import re
import json
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neu_auth import NEUAuthClient


# ── 数据模型 ──────────────────────────────────────────────────────────────────

@dataclass
class EvaluationTask:
    """评教任务（一级页面）"""
    task_id: str
    task_name: str
    total_count: int
    evaluated_count: int        # 已评门次 (ypcount)
    pending_count: int          # 未评门次 (wpcount)
    pjfs: str                   # 评价分数类型
    pjff: str                   # 评价方法
    status: int

    @classmethod
    def from_dict(cls, data: Dict) -> "EvaluationTask":
        return cls(
            task_id=data.get("taskid", ""),
            task_name=data.get("taskname", ""),
            total_count=int(data.get("count", 0) or 0),
            evaluated_count=int(data.get("ypcount", 0) or 0),
            pending_count=int(data.get("wpcount", 0) or 0),
            pjfs=str(data.get("pjfs", "")),
            pjff=str(data.get("pjff", "")),
            status=int(data.get("status", 0) or 0),
        )


@dataclass
class CourseEvaluation:
    """待评价课程（二级页面课程列表）"""
    xspjid: str                 # 学生评教ID（唯一标识每条课程评价记录）
    task_id: str                # 所属任务ID (libtaskid / rwid)
    task_name: str              # 任务名称
    course_name: str            # 课程名称
    teacher_name: str           # 教师姓名
    teacher_code: str           # 教师工号 (jg0101id)
    department: str             # 开课单位
    department_id: str          # 开课单位代码 (kkdwid / dwid)
    libid: str                  # 指标库ID
    course_type_code: str       # 课程属性代码 (kcsxcode)
    course_type_name: str       # 课程属性名称 (kcsxname)
    is_submit: str              # 是否已提交 ("0"=未提交, "1"=已提交)
    is_save: str                # 是否已保存
    is_kpj: str                 # 是否可评教
    score: Optional[float]      # 评分
    total_possible_score: float # 总分 (zbkzf=1000)
    avg_score: Optional[float]  # 平均分 (zpf)
    pjfs: str                   # 评价分数类型
    pjff: str                   # 评价方法
    begin_date: Optional[int]   # 开始时间戳(ms)
    end_date: Optional[int]     # 结束时间戳(ms)
    eval_session_id: str        # 评教教师ID (pjsjid)
    cpztcode: str               # 评价状态码
    pjsc: int                   # 评价次数
    pjrid: str                  # 评价人ID (学号)
    xnxqid: str                 # 学年学期
    raw_data: Dict = field(default_factory=dict)

    @property
    def is_evaluated(self) -> bool:
        # issubmit: "0"=已提交（已评），"1"=待提交（可评）
        return self.is_submit == "0"

    @classmethod
    def from_dict(cls, data: Dict) -> "CourseEvaluation":
        return cls(
            xspjid=data.get("xspjid", ""),
            task_id=data.get("taskid", ""),
            task_name=data.get("taskname", ""),
            course_name=data.get("kcmc", ""),
            teacher_name=data.get("jgxm", ""),
            teacher_code=data.get("jg0101id", ""),
            department=data.get("kkdwbm", ""),
            department_id=data.get("kkdwid", data.get("dwid", "")),
            libid=data.get("libid", ""),
            course_type_code=data.get("kcsxcode", ""),
            course_type_name=data.get("kcsxname", ""),
            is_submit=str(data.get("issubmit", "0")),
            is_save=str(data.get("issave", "0")),
            is_kpj=str(data.get("iskpj", "0")),
            score=data.get("score"),
            total_possible_score=float(data.get("score", 0) or 0),
            avg_score=data.get("zpf"),
            pjfs=str(data.get("pjfs", "")),
            pjff=str(data.get("pjff", "")),
            begin_date=data.get("begindate"),
            end_date=data.get("enddate"),
            eval_session_id=data.get("pjsjid", ""),
            cpztcode=str(data.get("cpztcode", "1")),
            pjsc=int(data.get("pjsc", 0) or 0),
            pjrid=str(data.get("pjrid", "")),
            xnxqid=str(data.get("xnxqid", "")),
            raw_data=data,
        )


@dataclass
class EvaluationIndicator:
    """评教指标项"""
    zbid: str                   # 指标ID
    zbmc: str                   # 指标名称（如"教学目标"、"教学内容"）
    evaltype: int               # 评价类型（1=选择型, 2=文本型）
    sfdx: int                   # 是否多选（1=多选, 0=单选）
    sfbt: int                   # 是否必填（1=必填, 0=选填）
    weight: float               # 权重（百分比）
    fz: float                   # 分值
    jsjx: str                   # 教师教学参考描述
    sort: int                   # 排序号
    level_json: List[Dict] = field(default_factory=list)  # 等级选项
    dfdj: Any = None            # 选择的打分等级
    result: str = ""            # 文本评价结果
    parent_id: str = ""         # 父指标ID (fzbid)

    @classmethod
    def from_dict(cls, data: Dict) -> "EvaluationIndicator":
        level_json = []
        lj = data.get("leveljson", "")
        if isinstance(lj, str) and lj:
            try:
                level_json = json.loads(lj)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(lj, list):
            level_json = lj

        # 解析已提交的评分（对已评课程，远程API会返回 dfdj / score / result）
        dfdj = data.get("dfdj")
        if dfdj is not None and dfdj != "":
            if isinstance(dfdj, str):
                # 多选时可能是逗号分隔的字符串，如 "6,5"
                if "," in dfdj:
                    try:
                        dfdj = [int(x.strip()) for x in dfdj.split(",") if x.strip().isdigit()]
                    except ValueError:
                        dfdj = None
                elif dfdj.isdigit():
                    dfdj = int(dfdj)
                else:
                    dfdj = None
            elif isinstance(dfdj, list):
                try:
                    dfdj = [int(x) for x in dfdj]
                except (ValueError, TypeError):
                    dfdj = None
        else:
            dfdj = None

        # 文本评价结果可能叫 result 或 subjectContent
        result = data.get("result", data.get("subjectContent", "")) or ""

        return cls(
            zbid=data.get("zbid", ""),
            zbmc=data.get("zbmc", ""),
            evaltype=int(data.get("evaltype", 0) or 0),
            sfdx=int(data.get("sfdx", 0) or 0),
            sfbt=int(data.get("sfbt", 0) or 0),
            weight=float(data.get("weight", 0) or 0),
            fz=float(data.get("fz", 0) or 0),
            jsjx=data.get("jsjx", ""),
            sort=int(data.get("sort", 0) or 0),
            level_json=level_json,
            dfdj=dfdj,
            result=result,
            parent_id=data.get("fzbid", "root"),
        )


@dataclass
class EvaluationTarget:
    """评教指标体系（一棵指标树）"""
    libid: str                  # 指标库ID
    libname: str                # 指标库名称（如"本科生评教指标（理论课）"）
    preface: str                # 评教说明文字
    total_score: float          # 指标总分 (zbkzf)
    can_quality_confirm: bool   # 是否可开启指标权重 (sfkqzbqz)
    indicators: List[EvaluationIndicator] = field(default_factory=list)
    raw_data: Dict = field(default_factory=dict)


# ── 评分策略 ──────────────────────────────────────────────────────────────────

class ScoringStrategy:
    """
    评分策略

    最高分策略：首题打5分，其余打6分（避免全部相同被拦截）
    最低分策略：首题打2分，其余打1分（同理）
    自定义策略：用户指定每个打分项的分数

    等级映射：6=100, 5=90, 4=80, 3=70, 2=60, 1=50
    """
    SCORE_MAP = {6: 100, 5: 90, 4: 80, 3: 70, 2: 60, 1: 50}

    @classmethod
    def highest(cls, indicators: List[EvaluationIndicator]) -> List[EvaluationIndicator]:
        """最高分策略：首题5分，其余6分"""
        scored = []
        selection_count = 0
        for ind in indicators:
            new_ind = EvaluationIndicator(
                zbid=ind.zbid, zbmc=ind.zbmc, evaltype=ind.evaltype,
                sfdx=ind.sfdx, sfbt=ind.sfbt, weight=ind.weight,
                fz=ind.fz, jsjx=ind.jsjx, sort=ind.sort,
                level_json=ind.level_json, parent_id=ind.parent_id,
            )
            if ind.evaltype == 1:
                if ind.sfdx == 1:
                    new_ind.dfdj = [6, 5]
                else:
                    new_ind.dfdj = 5 if selection_count == 0 else 6
                selection_count += 1
            scored.append(new_ind)
        return scored

    @classmethod
    def lowest(cls, indicators: List[EvaluationIndicator]) -> List[EvaluationIndicator]:
        """最低分策略：首题2分，其余1分"""
        scored = []
        selection_count = 0
        for ind in indicators:
            new_ind = EvaluationIndicator(
                zbid=ind.zbid, zbmc=ind.zbmc, evaltype=ind.evaltype,
                sfdx=ind.sfdx, sfbt=ind.sfbt, weight=ind.weight,
                fz=ind.fz, jsjx=ind.jsjx, sort=ind.sort,
                level_json=ind.level_json, parent_id=ind.parent_id,
            )
            if ind.evaltype == 1:
                if ind.sfdx == 1:
                    new_ind.dfdj = [1, 2] if selection_count == 0 else [1]
                else:
                    new_ind.dfdj = 2 if selection_count == 0 else 1
                selection_count += 1
            scored.append(new_ind)
        return scored

    @classmethod
    def custom(cls, indicators: List[EvaluationIndicator],
               score_map: Dict[str, int]) -> List[EvaluationIndicator]:
        scored = []
        for ind in indicators:
            new_ind = EvaluationIndicator(
                zbid=ind.zbid, zbmc=ind.zbmc, evaltype=ind.evaltype,
                sfdx=ind.sfdx, sfbt=ind.sfbt, weight=ind.weight,
                fz=ind.fz, jsjx=ind.jsjx, sort=ind.sort,
                level_json=ind.level_json, parent_id=ind.parent_id,
                dfdj=ind.dfdj, result=ind.result,
            )
            custom_score = score_map.get(ind.zbid) or score_map.get(ind.zbmc)
            if custom_score is not None and ind.evaltype == 1:
                if ind.sfdx == 1 and isinstance(custom_score, list):
                    new_ind.dfdj = custom_score
                else:
                    new_ind.dfdj = int(custom_score)
            scored.append(new_ind)
        return scored


# ── API 类 ────────────────────────────────────────────────────────────────────

class EvaluationAPI:
    """
    教学质量评价系统 API（zljk.neu.edu.cn）
    JWT 认证
    """

    BASE_URL = "http://zljk.neu.edu.cn"
    API_PREFIX = "/api"

    HEADERS = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Referer": "http://zljk.neu.edu.cn/evaluate/studentEvaluate/student-jdp/index",
        "Origin": "http://zljk.neu.edu.cn",
    }

    def __init__(self, client: NEUAuthClient):
        self._client = client
        self._jwt_token: Optional[str] = None
        self._jwt_exp: float = 0  # JWT 过期时间

    def _ensure_jwt(self) -> Optional[str]:
        """确保已获取有效的 JWT token"""
        # 检查现有 token 是否过期（提前60秒刷新）
        now = time.time()
        if self._jwt_token and self._jwt_exp > now + 60:
            return self._jwt_token

        try:
            service_url = "http://zljk.neu.edu.cn/caslogin"
            resp = self._client.get(
                f"https://pass.neu.edu.cn/tpass/login?service={service_url}",
                allow_redirects=False, timeout=30
            )
            location = resp.headers.get("Location", "")
            match = re.search(r'ticket=([^&]+)', location)
            if not match:
                print(f"[Evaluation] 无法提取 CAS ticket")
                return None
            service_ticket = match.group(1)

            resp = self._client.get(
                f"{self.BASE_URL}{self.API_PREFIX}/cas_ticket_verify?ticket={service_ticket}",
                timeout=30
            )
            data = resp.json()
            self._jwt_token = data.get("accessToken")
            if not self._jwt_token:
                print(f"[Evaluation] JWT 响应异常: {data}")
                return None

            # 解析 JWT 获取过期时间（exp 字段在 payload 中）
            try:
                payload_b64 = self._jwt_token.split(".")[1]
                # 补齐 base64 padding
                payload_b64 += "=" * (4 - len(payload_b64) % 4)
                payload = json.loads(__import__("base64").urlsafe_b64decode(payload_b64))
                self._jwt_exp = payload.get("exp", now + 3600)
            except Exception:
                self._jwt_exp = now + 3600  # 默认1小时

            return self._jwt_token

        except Exception as e:
            print(f"[Evaluation] 获取 JWT 失败: {e}")
            return None

    def _api_headers(self) -> Dict[str, str]:
        """构造带 JWT 的请求头"""
        jwt = self._ensure_jwt()
        headers = dict(self.HEADERS)
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"
            headers["X-Tenant-ID"] = "default"
        return headers

    def _api_url(self, path: str) -> str:
        return f"{self.BASE_URL}{self.API_PREFIX}{path}"

    def _default_page(self, page_size: int = 20, enable_pagination: bool = False) -> Dict:
        """构造默认分页参数"""
        return {
            "conditions": "",
            "custom": {} if enable_pagination else {},
            "orderBy": "",
            "pageIndex": 1,
            "pageSize": page_size,
            "paginationEnable": enable_pagination,
        }

    # ── 学期周期 ──────────────────────────────────────────────────────────

    def get_cycles(self) -> List[Dict]:
        """
        获取所有学年学期周期

        POST /api/qsmart/datacenter/TSysCycleController/tSysCycle/findCycle

        Returns:
            学期列表，每项包含 cycleid, value(如"2025-2026-2"), isdefault(是否默认)
        """
        url = f"{self.BASE_URL}/api/qsmart/datacenter/TSysCycleController/tSysCycle/findCycle"
        try:
            resp = self._client.post(url, json=None, headers=self._api_headers(), timeout=30)
            data = resp.json()
            if resp.status_code != 200:
                print(f"[Evaluation] get_cycles 失败: {data}")
                return []
            if not isinstance(data, list):
                print(f"[Evaluation] get_cycles 返回格式异常: {type(data)}")
                return []
            return [
                {
                    "cycleid": item.get("cycleid", ""),
                    "value": item.get("value", ""),
                    "isdefault": item.get("isdefault", 0),
                }
                for item in data
            ]
        except Exception as e:
            print(f"[Evaluation] 获取学期周期失败: {e}")
            return []

    def get_default_cycle(self) -> str:
        """
        获取当前默认学年学期

        优先返回 isdefault=1 的学期，找不到则返回列表第一项，都失败返回 "2025-2026-2" 兜底。
        """
        cycles = self.get_cycles()
        # 优先取 isdefault=1
        for c in cycles:
            if c.get("isdefault") == 1:
                return c["value"]
        # 兜底取第一项
        if cycles:
            return cycles[0]["value"]
        return "2025-2026-2"

    # ── 一级页面：任务列表 ──────────────────────────────────────────────────

    def get_tasks(self, xnxq: str = None) -> List[EvaluationTask]:
        """
        获取学生评教任务列表（一级页面）

        POST /api/TSysDdJxapController/tSysDdJxap/findJdpXqByTaskidHZ

        Args:
            xnxq: 学年学期，如 "2025-2026-2"。不传则自动获取默认学期。
        """
        if not xnxq:
            xnxq = self.get_default_cycle()
        url = self._api_url("/TSysDdJxapController/tSysDdJxap/findJdpXqByTaskidHZ")
        body = {
            "page": self._default_page(page_size=20, enable_pagination=False),
            "xnxq": xnxq,
        }
        try:
            resp = self._client.post(url, json=body, headers=self._api_headers(), timeout=30)
            data = resp.json()
            if resp.status_code != 200:
                print(f"[Evaluation] get_tasks 失败: {data}")
                return []
            items = data.get("items", [])
            return [EvaluationTask.from_dict(item) for item in items]
        except Exception as e:
            print(f"[Evaluation] 获取评教任务失败: {e}")
            return []

    # ── 二级页面：课程列表 ──────────────────────────────────────────────────

    def get_courses(self, task_id: str, xnxq: str = None,
                    course_name: str = "", teacher_name: str = "") -> List[CourseEvaluation]:
        """
        获取任务下的待评价课程列表（二级页面）

        POST /api/TSysDdJxapController/tSysDdJxap/findJdpXqByTaskid

        Args:
            task_id: 任务ID
            xnxq: 学年学期。不传则自动获取默认学期。
            course_name: 课程名称筛选
            teacher_name: 教师姓名筛选

        Returns:
            课程列表
        """
        if not xnxq:
            xnxq = self.get_default_cycle()
        url = self._api_url("/TSysDdJxapController/tSysDdJxap/findJdpXqByTaskid")
        body = {
            "page": self._default_page(page_size=100, enable_pagination=False),
            "kcmc": course_name,
            "jgxm": teacher_name,
            "xnxq": xnxq,
            "taskid": task_id,
        }
        try:
            resp = self._client.post(url, json=body, headers=self._api_headers(), timeout=30)
            data = resp.json()
            if resp.status_code != 200:
                print(f"[Evaluation] get_courses 失败: {data}")
                return []
            items = data.get("items", [])
            return [CourseEvaluation.from_dict(item) for item in items]
        except Exception as e:
            print(f"[Evaluation] 获取课程列表失败: {e}")
            return []

    def get_evaluated_info(self, task_id: str) -> List[Dict]:
        """
        获取已保存的评教信息（用于判断已评/未评状态）

        POST /api/TSysDdJxapController/findPjDxqk

        Args:
            task_id: 任务ID
        """
        url = self._api_url("/TSysDdJxapController/findPjDxqk")
        body = {"taskid": task_id}
        try:
            resp = self._client.post(url, json=body, headers=self._api_headers(), timeout=30)
            data = resp.json()
            if resp.status_code != 200:
                return []
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[Evaluation] 获取评教信息失败: {e}")
            return []

    # ── 评教指标体系 ──────────────────────────────────────────────────────

    def get_evaluation_target(self, task_id: str,
                              xspjid: str = "",
                              xnxqid: str = "") -> Optional[EvaluationTarget]:
        """
        获取评教指标体系（评分细则）

        GET /api/TSysDdEvalTargetController/queryEvalTargetTreeForEval
            ?libid=...&tkid=&taskid=...

        需要 libid（指标库ID），通过 get_tasklib 获取。

        Args:
            task_id: 任务ID
            xspjid: 学生评教ID（可选，传具体课程则只返回该课程的指标）
            xnxqid: 学年学期（如 "2025-2026-2"，不传或传空则自动获取默认学期）

        Returns:
            评教指标体系
        """
        # 空值兜底：自动获取默认学期
        if not xnxqid:
            xnxqid = self.get_default_cycle()

        # 先获取指标库ID
        lib_list = self.get_tasklib(task_id)
        if not lib_list:
            print(f"[Evaluation] 未找到指标库 (task_id={task_id})")
            return None

        libid = lib_list[0].get("id", "")
        libname = lib_list[0].get("libname", "")

        url = self._api_url("/TSysDdEvalTargetController/queryEvalTargetTreeForEval")
        params = {
            "libid": libid,
            "tkid": "",
            "taskid": xspjid,  # 注意：API 的 taskid 参数实际传 xspjid
            "xnxqid": xnxqid,
            "type": "student_jdp",
            "jxapid": "",
            "jg0101id": "",
            "libtaskid": task_id,
            "kkdwid": "",
            "kclbcode": "",
        }
        try:
            resp = self._client.post(url, params=params, headers=self._api_headers(), timeout=30)
            data = resp.json()
            if resp.status_code != 200:
                print(f"[Evaluation] 获取指标体系失败: {data}")
                return None

            indicators = []
            for item in data.get("list", []):
                indicators.append(EvaluationIndicator.from_dict(item))

            return EvaluationTarget(
                libid=libid,
                libname=libname,
                preface=data.get("preface", ""),
                total_score=float(data.get("zbkzf", 0) or 0),
                can_quality_confirm=data.get("sfkqzbqz") == "1",
                indicators=indicators,
                raw_data=data,
            )
        except Exception as e:
            print(f"[Evaluation] 获取指标体系异常: {e}")
            return None

    def get_tasklib(self, task_id: str) -> List[Dict]:
        """
        获取任务关联的指标库

        POST /api/TSysDdKbxxController/getTasklib/{task_id}

        Args:
            task_id: 任务ID
        """
        url = self._api_url(f"/TSysDdKbxxController/getTasklib/{task_id}")
        try:
            resp = self._client.post(url, json={}, headers=self._api_headers(), timeout=30)
            data = resp.json()
            if resp.status_code != 200:
                return []
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[Evaluation] 获取指标库失败: {e}")
            return []

    def get_eval_config(self, task_id: str) -> Dict:
        """
        获取评教开关配置

        POST /api/TSysDdJxapBcpController/queryKzInfo

        Args:
            task_id: 任务ID
        """
        url = self._api_url("/TSysDdJxapBcpController/queryKzInfo")
        body = {"taskid": task_id}
        try:
            resp = self._client.post(url, json=body, headers=self._api_headers(), timeout=30)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            print(f"[Evaluation] 获取评教配置失败: {e}")
            return {}

    def get_task_state(self, task_id: str, xnxq: str = None) -> Dict:
        """
        获取评教任务状态

        POST /api/TSysDdJxapController/findPjTaskState

        Args:
            task_id: 任务ID
            xnxq: 学年学期。不传则自动获取默认学期。
        """
        if not xnxq:
            xnxq = self.get_default_cycle()
        url = self._api_url("/TSysDdJxapController/findPjTaskState")
        body = {"taskid": task_id, "xnxqid": xnxq}
        try:
            resp = self._client.post(url, json=body, headers=self._api_headers(), timeout=30)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            print(f"[Evaluation] 获取任务状态失败: {e}")
            return {}

    # ── 评分与提交 ────────────────────────────────────────────────────────

    SCORE_MAP: Dict[int, int] = {6: 100, 5: 90, 4: 80, 3: 70, 2: 60, 1: 50}

    def build_submit_data(self, course: CourseEvaluation, target: EvaluationTarget,
                          strategy: str = "highest",
                          custom_scores: Optional[Dict[str, int]] = None) -> Optional[Dict]:
        """
        构建评教提交数据

        提交 API: POST /api/TSysDdEvalResultJdpController/tSysDdEvalResultJdp/saveJdp

        请求体格式（已通过浏览器抓取确认）:
        {
          "resultList": [
            {
              "id": "",              // 空（新提交）
              "taskid": xspjid,      // 学生评教ID（⚠️注意字段名与 libtaskid 不同）
              "zbid": 指标ID,        // 评教指标ID
              "dfdj": "5",           // 打分等级（字符串）
              "subjectContent": "",  // 文本内容（文本型指标）
              "xnxqid": course.xnxqid,  // 学年学期（从课程数据自动获取）
              "zblx": "",            // 指标类型（空）
              "rwid": task_id,       // 任务ID（libtaskid）
              "kkdwid": "020500",    // 开课单位代码
              "dwid": "020500",      // 单位代码
              "libid": 指标库ID,
              "score": 90,           // 得分（int，由 dfdj 换算）
              "evaltype": 1,         // 1=选择型, 2=文本型
              "mlid": "",
              "mlmc": "",
              "pjsjid": 评教教师ID,
              "cpztcode": "1",       // 评价状态码
              "issubmit": 0          // 0=未提交
            }, ...
          ],
          "task": {
            "id": xspjid,           // 学生评教ID
            "zpf": 99,              // 总评均分（float）
            "issave": "0",
            "issubmit": 0,
            "libid": 指标库ID,
            "dfyy": "",             // 打分原因（空）
            "pjsc": 8               // 评价次数
          }
        }

        Args:
            course: 课程评教信息（CourseEvaluation）
            target: 评教指标体系（EvaluationTarget）
            strategy: 评分策略 "highest" / "lowest" / "custom"
            custom_scores: 自定义分数映射 {指标ID/名称: 分数}
        """
        indicators = target.indicators
        if strategy == "highest":
            scored = ScoringStrategy.highest(indicators)
        elif strategy == "lowest":
            scored = ScoringStrategy.lowest(indicators)
        elif strategy == "custom":
            scored = ScoringStrategy.custom(indicators, custom_scores or {})
        else:
            return None

        result_list = []
        total_score = 0
        score_count = 0

        for ind in scored:
            item = {
                "id": "",
                "taskid": course.xspjid,          # 注意：这里 taskid 实际是 xspjid
                "zbid": ind.zbid,
                "subjectContent": "",
                "xnxqid": course.xnxqid,
                "zblx": "",
                "rwid": course.task_id,           # 实际的任务ID (libtaskid)
                "kkdwid": course.department_id,
                "dwid": course.department_id,
                "libid": target.libid,
                "evaltype": ind.evaltype,
                "mlid": "",
                "mlmc": "",
                "pjsjid": course.eval_session_id,
                "cpztcode": course.cpztcode,
                "issubmit": 0,
                "score": 0,
                "dfdj": "",
            }

            if ind.evaltype == 1:
                # 选择型指标
                if ind.sfdx == 1 and isinstance(ind.dfdj, list):
                    # 多选：取最高分
                    dfdj = str(max(ind.dfdj))
                elif ind.dfdj is not None:
                    dfdj = str(ind.dfdj)
                else:
                    dfdj = ""
                item["dfdj"] = dfdj
                item["score"] = self.SCORE_MAP.get(int(dfdj), 0) if dfdj.isdigit() else 0
                total_score += item["score"]
                score_count += 1
            else:
                # 文本型指标
                item["subjectContent"] = ind.result or ""
                item["dfdj"] = ""
                item["score"] = 0

            result_list.append(item)

        # 计算总评均分
        avg_score = round(total_score / score_count, 1) if score_count > 0 else 0

        return {
            "resultList": result_list,
            "task": {
                "id": course.xspjid,
                "zpf": avg_score,
                "issave": "0",
                "issubmit": 0,
                "libid": target.libid,
                "dfyy": "",
                "pjsc": course.pjsc,
            },
        }

    def submit_evaluation(self, course: CourseEvaluation, target: EvaluationTarget,
                          strategy: str = "highest",
                          custom_scores: Optional[Dict[str, int]] = None) -> Dict:
        """
        提交评教结果

        POST /api/TSysDdEvalResultJdpController/tSysDdEvalResultJdp/saveJdp

        ⚠️ 评教系统不支持重试！提交前请确认数据正确。

        Args:
            course: 课程评教信息（CourseEvaluation，需包含 xspjid, task_id 等）
            target: 评教指标体系（EvaluationTarget，需包含 libid, indicators）
            strategy: 评分策略 "highest" / "lowest" / "custom"
            custom_scores: 自定义分数映射

        Returns:
            {"success": True/False, "message": "...", "data": ...}
        """
        # 构建提交数据
        submit_data = self.build_submit_data(course, target, strategy, custom_scores)
        if not submit_data:
            return {"success": False, "message": "构建提交数据失败"}

        # 验证评分
        indicators = target.indicators
        if strategy == "highest":
            scored = ScoringStrategy.highest(indicators)
        elif strategy == "lowest":
            scored = ScoringStrategy.lowest(indicators)
        else:
            scored = ScoringStrategy.custom(indicators, custom_scores or {})

        validation = self._validate_scoring(scored)
        if not validation["valid"]:
            return {"success": False, "message": "评分验证失败", "errors": validation["errors"]}

        # 发送提交请求
        url = self._api_url("/TSysDdEvalResultJdpController/tSysDdEvalResultJdp/saveJdp")
        try:
            resp = self._client.post(url, json=submit_data, headers=self._api_headers(), timeout=30)
            data = resp.json()

            if resp.status_code == 200 and data == "success":
                avg = submit_data["task"]["zpf"]
                print(f"[Evaluation] 评教提交成功! 课程: {course.course_name} 教师: {course.teacher_name} 均分: {avg}")
                return {"success": True, "message": "评教提交成功", "data": submit_data}

            # 处理已知错误
            if isinstance(data, dict):
                msg = data.get("message", str(data))
                if "重复提交" in msg:
                    print(f"[Evaluation] 该课程已评过: {course.course_name}")
                    return {"success": True, "message": "该课程已评价过（重复提交）", "skipped": True}
                if "评价不能全部相同" in msg or "全部相同" in msg:
                    return {"success": False, "message": "评分验证失败：评价选项不能全部相同", "data": data}

            print(f"[Evaluation] 评教提交失败: {data}")
            return {"success": False, "message": f"提交失败: {data}", "data": data}

        except Exception as e:
            print(f"[Evaluation] 评教提交异常: {e}")
            return {"success": False, "message": f"提交异常: {e}"}

    def evaluate_course(self, course: CourseEvaluation,
                        strategy: str = "highest",
                        custom_scores: Optional[Dict[str, int]] = None) -> Dict:
        """
        一站式评教：获取指标体系 → 评分 → 提交

        自动完成从获取指标到提交的全部流程。

        Args:
            course: 课程评教信息
            strategy: 评分策略
            custom_scores: 自定义分数

        Returns:
            提交结果
        """
        # 检查是否已评
        if course.is_evaluated:
            return {"success": False, "message": f"该课程已评价: {course.course_name}"}

        # 获取指标体系
        target = self.get_evaluation_target(course.task_id, course.xspjid, course.xnxqid)
        if not target:
            return {"success": False, "message": "获取评教指标体系失败"}

        # 提交
        return self.submit_evaluation(course, target, strategy, custom_scores)

    def evaluate_all_pending(self, task_id: str, xnxq: str = None,
                            strategy: str = "highest",
                            custom_scores: Optional[Dict[str, int]] = None,
                            delay: float = 2.0) -> List[Dict]:
        """
        批量评教：评价任务下所有未评课程

        每次提交之间间隔 delay 秒，避免请求过快。

        Args:
            task_id: 任务ID
            xnxq: 学年学期
            strategy: 评分策略
            custom_scores: 自定义分数
            delay: 提交间隔（秒）

        Returns:
            每门课程的提交结果列表
        """
        results = []
        courses = self.get_courses(task_id, xnxq)
        pending = [c for c in courses if not c.is_evaluated]

        if not pending:
            print(f"[Evaluation] 没有待评课程")
            return results

        print(f"[Evaluation] 待评课程: {len(pending)} 门")

        for i, course in enumerate(pending):
            print(f"\n[Evaluation] [{i+1}/{len(pending)}] {course.course_name} - {course.teacher_name}")
            result = self.evaluate_course(course, strategy, custom_scores)
            results.append({
                "course_name": course.course_name,
                "teacher_name": course.teacher_name,
                **result,
            })

            if result["success"]:
                print(f"  ✓ 成功")
            else:
                print(f"  ✗ 失败: {result['message']}")

            # 间隔
            if i < len(pending) - 1:
                time.sleep(delay)

        # 统计
        success_count = sum(1 for r in results if r["success"])
        print(f"\n[Evaluation] 完成: {success_count}/{len(pending)} 门成功")

        return results

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def _validate_scoring(self, indicators: List[EvaluationIndicator]) -> Dict:
        errors = []
        selection_scores = []

        for ind in indicators:
            if ind.evaltype == 1:
                if ind.sfbt == 1 and not ind.dfdj:
                    errors.append(f"必填指标未评分: {ind.zbmc}")
                if isinstance(ind.dfdj, list):
                    selection_scores.extend(ind.dfdj)
                elif ind.dfdj is not None:
                    selection_scores.append(ind.dfdj)
            elif ind.sfbt == 1 and not ind.result:
                errors.append(f"必填文本指标未填写: {ind.zbmc}")

        if len(selection_scores) > 1:
            if len(set(str(s) for s in selection_scores)) == 1:
                errors.append("评价选项不能全部相同")

        return {"valid": len(errors) == 0, "errors": errors}
