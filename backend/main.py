"""
backend/main.py
===============
FastAPI 后端服务

教务系统工具箱 API
- 智能合并本地和远程成绩
- 支持列筛选配置
- 完整的日志记录
"""

import os
import sys
from typing import Optional, List, Dict, Any
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from neu_auth import NEUAuthClient
from neu_storage import Storage, AcademicStorage, AutoLoginManager, AcademicReportStorage
from neu_log import setup_logging, LogConfig, LogCategory, LogLevel, get_logger
from neu_log.access_logger import FastAPILogMiddleware
from neu_log.manager import LogManager

# ── 初始化（在创建 FastAPI 应用之前）────────────────────────────────────────────

# 全局状态
_auth_client: Optional[NEUAuthClient] = None
_storage = Storage()
_academic_storage = AcademicStorage(_storage)

# Cookie 持久化文件路径
COOKIE_FILE = os.path.join(_storage.config.data_dir, "session.pkl")

# 初始化日志系统（必须在添加中间件之前）
_log_config = LogConfig(
    log_dir=os.path.join(_storage.config.data_dir, "logs"),
    level=LogLevel.INFO,
    console_output=True,
)
setup_logging(_log_config)

# 初始化自动登录管理器（传入 cookie 文件路径）
_auto_login = AutoLoginManager(_storage, cookie_file=COOKIE_FILE)

# 初始化日志管理器
_log_manager = LogManager(_log_config)

# API 错误日志记录器
_api_logger = get_logger("api", LogCategory.SYSTEM, _log_config)

# 初始化培养计划存储
_report_storage = AcademicReportStorage(_storage)

# ── FastAPI 应用 ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEU教务系统工具箱 API",
    description="东北大学教务系统工具箱后端服务",
    version="1.2.0"
)

# 日志中间件（必须在 CORS 之前）
app.add_middleware(FastAPILogMiddleware, config=_log_config)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 数据模型 ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


class LoginResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None


class CourseScoreModel(BaseModel):
    """完整的成绩模型 - 包含所有字段"""
    name: str
    code: str
    score: str = Field(..., description="成绩（数字或文字等级如'优'、'合格'）")
    score_value: float = Field(..., description="用于计算的数值（绩点+5)*10")
    gpa: float
    credit: float
    term: str
    term_display: str = Field(..., description="学期显示名，如'2024-2025学年春季学期'")
    course_type: str = Field(..., description="课程性质，如'必修/选修'")
    course_category: str = Field(..., description="课程类别，如'人文社会科学类'")
    general_category: str = Field(default="", description="通识选修类别，如'科学素养类'")
    exam_type: str = Field(..., description="考核方式，如'考试/考查'")
    exam_status: str = Field(default="", description="考试状态，如'初修/重修'")
    course_nature: str = Field(default="", description="课程性质代码")
    is_passed: bool


class TermScoresModel(BaseModel):
    term_code: str
    term_name: str
    courses: List[CourseScoreModel]
    total_credits: float
    gpa: float


class ScoresResponse(BaseModel):
    total_courses: int
    overall_gpa: Optional[float]
    calculated_gpa: float
    source: str = Field(..., description="数据来源: local/remote")
    is_fresh: bool = Field(..., description="是否最新数据")
    last_update: Optional[datetime] = None
    scores: List[CourseScoreModel]


class ColumnConfig(BaseModel):
    """列显示配置"""
    key: str
    title: str
    visible: bool = True
    width: Optional[int] = None


# ── 依赖函数 ──────────────────────────────────────────────────────────────────

def get_auth_client() -> Optional[NEUAuthClient]:
    """
    获取当前认证客户端
    
    恢复优先级：
    1. 内存中的客户端（如果有效）
    2. 尝试用保存的 Cookie 恢复（免密）
    3. 尝试用保存的密码重新登录
    """
    global _auth_client
    
    # 1. 检查内存中的客户端
    if _auth_client is not None:
        # 尝试确保登录（内部会优先用 Cookie 刷新）
        if _auth_client.ensure_login():
            return _auth_client
    
    # 2. 尝试加载保存的凭证并创建客户端
    creds = _storage.load_credentials()
    if creds:
        username, password = creds
        # 创建客户端时会自动尝试从 Cookie 文件恢复
        client = NEUAuthClient(
            username=username, 
            password=password,
            cookie_file=COOKIE_FILE
        )
        # 尝试登录（内部会优先用 Cookie 刷新票据）
        if client.ensure_login():
            _auth_client = client
            return _auth_client
    
    return None


def require_auth() -> NEUAuthClient:
    """需要登录的依赖"""
    client = get_auth_client()
    if client is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return client


# ── API 路由 ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """根路径"""
    return {"message": "NEU教务系统工具箱 API", "version": "1.1.0"}


@app.get("/api/status")
async def get_status():
    """获取登录状态和存储信息"""
    client = get_auth_client()
    
    storage_info = _storage.get_storage_info()
    has_credentials = _storage.load_credentials() is not None
    last_update = _storage.get_last_update_time()
    
    return {
        "is_logged_in": client is not None and client.is_logged_in,
        "has_credentials": has_credentials,
        "has_local_data": storage_info["csv_count"] > 0,
        "last_update": last_update.isoformat() if last_update else None,
        "storage": storage_info,
        "current_user": client.username if client else None
    }


@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """登录接口"""
    global _auth_client
    
    try:
        # 创建客户端，启用 Cookie 持久化
        client = NEUAuthClient(
            request.username, 
            request.password,
            cookie_file=COOKIE_FILE
        )
        success = client.login()
        
        if success:
            _auth_client = client
            
            # 保存凭证
            if request.remember:
                _auto_login.save_login(client)
            
            # 自动获取并保存成绩（后台执行，不阻塞登录）
            try:
                _academic_storage.refresh_scores(client)
            except Exception as e:
                print(f"自动保存成绩失败: {e}")
            
            return LoginResponse(
                success=True,
                message="登录成功",
                username=request.username
            )
        else:
            return LoginResponse(
                success=False,
                message="登录失败"
            )
    
    except Exception as e:
        return LoginResponse(
            success=False,
            message=f"登录错误: {str(e)}"
        )


@app.post("/api/logout")
async def logout(clear_data: bool = Query(True, description="是否清理用户数据")):
    """
    登出接口
    
    Args:
        clear_data: 是否清理用户数据（成绩、培养计划、头像等），默认 True
    """
    global _auth_client
    
    result = {"success": True, "message": "已登出"}
    
    # 清除客户端的 cookie
    if _auth_client:
        _auth_client.clear_cookies()
    
    _auth_client = None
    _auto_login.clear_login()
    
    # 清理用户数据（保留登录配置）
    if clear_data:
        try:
            clear_result = _storage.clear_all_data(preserve_config=True)
            _api_logger.info(f"[Logout] 清理数据: 删除 {clear_result['deleted_count']} 个文件, 保留 {clear_result['preserved_count']} 个配置")
            result["data_cleared"] = True
            result["cleared_files"] = clear_result["deleted_count"]
        except Exception as e:
            _api_logger.error(f"[Logout] 清理数据失败: {e}")
            result["data_cleared"] = False
            result["clear_error"] = str(e)
    
    return result


@app.get("/api/scores", response_model=ScoresResponse)
async def get_scores(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取成绩列表 - 智能合并本地和远程数据
    
    - 默认使用本地缓存
    - 缓存过期(3天)或refresh=true时自动拉取云端
    """
    try:
        result = _academic_storage.get_scores_smart(auth, force_refresh=refresh)
        scores = result["scores"]
        
        # 转换为模型
        score_models = [
            CourseScoreModel(
                name=s.name,
                code=s.code,
                score=s.score,
                score_value=s.get_score_value(),
                gpa=s.gpa,
                credit=s.credit,
                term=s.term,
                term_display=s.term_display,
                course_type=s.course_type,
                course_category=s.course_category,
                general_category=s.general_category,
                exam_type=s.exam_type,
                exam_status=s.exam_status,
                course_nature=s.course_nature,
                is_passed=s.is_passed
            )
            for s in scores
        ]
        
        return ScoresResponse(
            total_courses=len(scores),
            overall_gpa=result.get("overall_gpa"),
            calculated_gpa=sum(s.gpa * s.credit for s in scores) / sum(s.credit for s in scores) if scores else 0.0,
            source=result["source"],
            is_fresh=result["is_fresh"],
            last_update=result.get("last_update"),
            scores=score_models
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取成绩失败: {str(e)}")


@app.get("/api/scores/by-term", response_model=List[TermScoresModel])
async def get_scores_by_term(auth: NEUAuthClient = Depends(require_auth)):
    """按学期获取成绩"""
    try:
        term_scores = auth.academic.get_scores_by_term()
        
        result = []
        for ts in term_scores:
            courses = [
                CourseScoreModel(
                    name=c.name,
                    code=c.code,
                    score=c.score,
                    score_value=c.get_score_value(),
                    gpa=c.gpa,
                    credit=c.credit,
                    term=c.term,
                    term_display=c.term_display,
                    course_type=c.course_type,
                    course_category=c.course_category,
                    general_category=c.general_category,
                    exam_type=c.exam_type,
                    exam_status=c.exam_status,
                    course_nature=c.course_nature,
                    is_passed=c.is_passed
                )
                for c in ts.courses
            ]
            
            result.append(TermScoresModel(
                term_code=ts.term_code,
                term_name=ts.term_name,
                courses=courses,
                total_credits=ts.total_credits,
                gpa=ts.gpa
            ))
        
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取成绩失败: {str(e)}")


@app.post("/api/scores/refresh")
async def refresh_scores(auth: NEUAuthClient = Depends(require_auth)):
    """手动刷新成绩数据"""
    result = _academic_storage.refresh_scores(auth)
    
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])


@app.get("/api/columns/default")
async def get_default_columns() -> List[ColumnConfig]:
    """获取默认列配置"""
    return [
        ColumnConfig(key="name", title="课程名称", visible=True, width=200),
        ColumnConfig(key="code", title="课程代码", visible=True, width=120),
        ColumnConfig(key="score", title="成绩", visible=True, width=80),
        ColumnConfig(key="gpa", title="绩点", visible=True, width=80),
        ColumnConfig(key="credit", title="学分", visible=True, width=80),
        ColumnConfig(key="term_display", title="学期", visible=True, width=180),
        ColumnConfig(key="course_type", title="课程性质", visible=True, width=100),
        ColumnConfig(key="course_category", title="课程类别", visible=False, width=150),
        ColumnConfig(key="general_category", title="通识类别", visible=False, width=150),
        ColumnConfig(key="exam_type", title="考核方式", visible=False, width=100),
        ColumnConfig(key="exam_status", title="考试状态", visible=False, width=100),
        ColumnConfig(key="is_passed", title="状态", visible=True, width=80),
    ]


# ── 日志管理 API ───────────────────────────────────────────────────────────────

class LogSummaryResponse(BaseModel):
    """日志摘要响应"""
    period_days: int
    total_files: int
    total_size_mb: float
    categories: Dict[str, Any]


class LogEntryResponse(BaseModel):
    """日志条目响应"""
    timestamp: str
    level: str
    logger: str
    message: str


class LogListResponse(BaseModel):
    """日志列表响应"""
    category: str
    date: str
    filename: str
    size_mb: float


@app.get("/api/logs/summary", response_model=LogSummaryResponse)
async def get_logs_summary(days: int = Query(7, ge=1, le=30)):
    """获取日志统计摘要"""
    return _log_manager.get_log_summary(days)


@app.get("/api/logs/files")
async def get_log_files(
    category: Optional[str] = Query(None, description="日志分类: system/access/error/login/sync"),
    days: int = Query(7, ge=1, le=30)
):
    """获取日志文件列表"""
    cat = LogCategory(category) if category else None
    files = _log_manager.get_log_files(category=cat, days=days)
    return [
        {
            "category": f.category,
            "date": f.date,
            "filename": f.filename,
            "size_mb": round(f.size_bytes / 1024 / 1024, 2),
            "modified_time": f.modified_time.isoformat(),
        }
        for f in files
    ]


@app.get("/api/logs/content")
async def get_log_content(
    category: str = Query(..., description="日志分类"),
    date: str = Query(..., description="日期 (YYYY-MM-DD)"),
    level: Optional[str] = Query(None, description="日志级别过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """获取日志内容"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")
    
    entries = _log_manager.read_log(cat, date, level=level, search=search, limit=limit, offset=offset)
    return {
        "category": category,
        "date": date,
        "total_returned": len(entries),
        "entries": [
            {
                "timestamp": e.timestamp,
                "level": e.level,
                "logger": e.logger,
                "message": e.message,
            }
            for e in entries
        ]
    }


@app.get("/api/logs/tail")
async def tail_log(
    category: str = Query(..., description="日志分类"),
    date: str = Query(..., description="日期 (YYYY-MM-DD)"),
    lines: int = Query(100, ge=1, le=500),
):
    """获取日志末尾 N 行"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")
    
    entries = _log_manager.tail_log(cat, date, lines)
    return {
        "category": category,
        "date": date,
        "lines": len(entries),
        "entries": [
            {
                "timestamp": e.timestamp,
                "level": e.level,
                "logger": e.logger,
                "message": e.message,
            }
            for e in entries
        ]
    }


@app.get("/api/logs/search")
async def search_logs(
    keyword: str = Query(..., description="搜索关键词"),
    category: Optional[str] = Query(None, description="日志分类过滤"),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(100, ge=1, le=500),
):
    """搜索日志"""
    cat = LogCategory(category) if category else None
    results = _log_manager.search_logs(keyword, cat, days, limit)
    return {"keyword": keyword, "total": len(results), "results": results}


@app.get("/api/logs/download/{category}/{date}")
async def download_log(category: str, date: str):
    """下载日志文件"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")
    
    content = _log_manager.download_log(cat, date)
    if content is None:
        raise HTTPException(status_code=404, detail="日志文件不存在")
    
    filename = f"{category}_{date}.log"
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.delete("/api/logs/cleanup")
async def cleanup_logs(keep_days: int = Query(30, ge=7, le=365)):
    """清理旧日志"""
    deleted = _log_manager.clear_old_logs(keep_days)
    return {"deleted_files": deleted, "keep_days": keep_days}


# ── 培养计划 API ───────────────────────────────────────────────────────────────

class CreditSummaryModel(BaseModel):
    """学分统计摘要"""
    total_required: float
    total_passed: float
    total_selected: float
    total_earned: float
    total_remaining: float
    completion_rate: float


class CourseModel(BaseModel):
    """课程模型"""
    course_name: str
    course_code: str
    course_nature: str
    credit: float
    score: Optional[str] = None
    is_passed: bool
    is_selected: bool
    is_planned: bool
    status: str
    status_display: str
    term_code: Optional[str] = None
    select_term_code: Optional[str] = None
    exam_type: Optional[str] = None
    is_core: bool = False
    substitute_course_name: Optional[str] = None
    substitute_credit: float = 0
    dept_code: Optional[str] = None
    category_path: List[str] = []


class CategoryNodeModel(BaseModel):
    """类别节点模型（支持递归嵌套）"""
    wid: str
    name: str
    category_code: str
    depth: int
    path: str
    path_array: List[str]
    is_leaf: bool
    required_credits: float
    passed_credits: float
    selected_credits: float
    planned_credits: float
    earned_credits: float
    remaining_credits: float
    completion_rate: float
    is_completed: bool
    courses: List[CourseModel]
    children: List[Any]  # 递归类型，使用Any


class AcademicReportResponse(BaseModel):
    """培养计划响应"""
    student_name: str
    student_id: str
    grade: str = ""
    college: str = ""
    major: str = ""
    class_name: str = ""
    expected_graduation: str = ""
    program_code: str
    program_name: str = ""
    calculated_time: str
    credit_summary: CreditSummaryModel
    categories: List[CategoryNodeModel]
    outside_courses: List[CourseModel]
    source: str = "remote"
    is_fresh: bool = True
    last_update: Optional[str] = None  # 本地保存时间 ISO 格式


@app.get("/api/academic-report", response_model=AcademicReportResponse)
async def get_academic_report(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取学业监测报告（培养计划）- 智能合并本地和远程数据
    
    - 默认优先使用本地缓存
    - 仅当成绩更新或 refresh=true 时拉取云端
    """
    try:
        result = _report_storage.get_report_smart(auth, force_refresh=refresh)
        report = result["report"]
        
        # 格式化 last_update 为 ISO 字符串
        last_update = result.get("last_update")
        if last_update and hasattr(last_update, 'isoformat'):
            last_update = last_update.isoformat()
        
        return AcademicReportResponse(
            student_name=report.get("student_name", ""),
            student_id=report.get("student_id", ""),
            grade=report.get("grade", ""),
            college=report.get("college", ""),
            major=report.get("major", ""),
            class_name=report.get("class_name", ""),
            expected_graduation=report.get("expected_graduation", ""),
            program_code=report.get("program_code", ""),
            program_name=report.get("program_name", ""),
            calculated_time=report.get("calculated_time", ""),
            credit_summary=report.get("credit_summary", {
                "total_required": 0,
                "total_passed": 0,
                "total_selected": 0,
                "total_earned": 0,
                "total_remaining": 0,
                "completion_rate": 0,
            }),
            categories=report.get("categories", []),
            outside_courses=report.get("outside_courses", []),
            source=result.get("source", "remote"),
            is_fresh=result.get("is_fresh", True),
            last_update=last_update
        )
    except Exception as e:
        import traceback
        print(f"获取培养计划错误: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"获取培养计划失败: {str(e)}")


@app.post("/api/academic-report/refresh")
async def refresh_academic_report(auth: NEUAuthClient = Depends(require_auth)):
    """手动刷新培养计划数据"""
    result = _report_storage.refresh_report(auth)
    
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])


@app.get("/api/academic-report/summary")
async def get_academic_report_summary(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取培养计划摘要信息（用于概览页面）
    """
    try:
        result = _report_storage.get_report_smart(auth, force_refresh=refresh)
        report = result["report"]
        
        if report is None:
            raise HTTPException(status_code=500, detail="获取培养计划失败")
        
        credit_summary = report.get("credit_summary", {})
        
        # 格式化 last_update 为 ISO 字符串
        last_update = result.get("last_update")
        if last_update and hasattr(last_update, 'isoformat'):
            last_update = last_update.isoformat()
        
        # 递归收集所有类别节点（包括子节点）
        def collect_categories(categories):
            result = []
            for cat in categories:
                cat_summary = {
                    "name": cat.get("name", ""),
                    "path": cat.get("path", ""),
                    "path_array": cat.get("path_array", []),
                    "is_leaf": cat.get("is_leaf", False),
                    "required_credits": cat.get("required_credits", 0),
                    "passed_credits": cat.get("passed_credits", 0),
                    "selected_credits": cat.get("selected_credits", 0),
                    "earned_credits": cat.get("earned_credits", 0),
                    "remaining_credits": cat.get("remaining_credits", 0),
                    "completion_rate": cat.get("completion_rate", 0),
                    "is_completed": cat.get("is_completed", False),
                    "course_count": len(cat.get("courses", [])),
                }
                result.append(cat_summary)
                # 递归收集子节点
                if cat.get("children"):
                    result.extend(collect_categories(cat.get("children", [])))
            return result
        
        return {
            "student_info": {
                "name": report.get("student_name", ""),
                "student_id": report.get("student_id", ""),
                "major": report.get("major", ""),
                "college": report.get("college", ""),
            },
            "program_info": {
                "name": report.get("program_name", ""),
                "code": report.get("program_code", ""),
            },
            "credit_summary": {
                "total_required": credit_summary.get("total_required", 0),
                "total_passed": credit_summary.get("total_passed", 0),
                "total_selected": credit_summary.get("total_selected", 0),
                "total_earned": credit_summary.get("total_earned", 0),
                "total_remaining": credit_summary.get("total_remaining", 0),
                "completion_rate": credit_summary.get("completion_rate", 0),
            },
            "category_summary": collect_categories(report.get("categories", [])),
            "calculated_time": report.get("calculated_time", ""),
            "source": result.get("source", "remote"),
            "is_fresh": result.get("is_fresh", True),
            "last_update": last_update,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取培养计划摘要失败: {str(e)}")


@app.get("/api/academic-report/export")
async def export_academic_report(auth: NEUAuthClient = Depends(require_auth)):
    """
    导出培养计划为 CSV
    """
    try:
        report = auth.academic_report.get_report()
        if report is None:
            raise HTTPException(status_code=500, detail="获取培养计划失败")
        
        files = auth.academic_report.export_to_csv(report, output_dir=_storage.config.data_dir)
        
        return {
            "success": True,
            "message": "导出成功",
            "files": files
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


# ── 实验选课 API ──────────────────────────────────────────────────────────────

@app.get("/api/experiment-courses")
async def get_experiment_courses(
    term: str = Query(None, description="学年学期代码，如 2025-2026-2"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取实验选课课程列表
    
    - term 不传则自动获取当前学期
    """
    try:
        print(f"[Experiment] 获取实验课程, term={term}")
        print(f"[Experiment] auth client: {auth}, username: {auth.username}")
        
        from neu_academic.experiment import ExperimentCourseAPI
        api = ExperimentCourseAPI(auth)
        
        # 如果没有传term，获取当前学期
        if not term:
            print("[Experiment] 未提供term，尝试自动获取...")
            term = api.get_semester()
            print(f"[Experiment] 自动获取学期: {term}")
        
        if not term:
            print("[Experiment] 无法获取学期，返回空列表")
            return {"courses": [], "term": "", "total": 0}
        
        print(f"[Experiment] 调用API获取课程，term={term}")
        courses = api.get_courses(term)
        print(f"[Experiment] 获取到 {len(courses)} 门课程")
        
        return {
            "courses": [
                {
                    "task_id": c.task_id,
                    "course_name": c.course_name,
                    "course_no": c.course_no,
                    "credit": c.credit,
                    "experiment_hours": c.experiment_hours,
                    "center_name": c.center_name,
                    "college_name": c.college_name,
                    "must_do_count": c.must_do_count,
                    "selected_count": c.selected_count,
                    "is_complete": c.is_complete,
                    "projects": [
                        {
                            "project_name": p.project_name,
                            "project_code": p.project_code,
                            "must_do": p.must_do,
                            "selected_round_id": p.selected_round_id,
                            "select_status": p.select_status,
                            "is_selected": bool(p.selected_round_id),
                        }
                        for p in c.projects
                    ]
                }
                for c in courses
            ],
            "term": term or api.get_semester(),
            "total": len(courses),
        }
    except Exception as e:
        import traceback
        error_msg = f"获取实验课程失败: {e}"
        trace = traceback.format_exc()
        _api_logger.error(error_msg)
        _api_logger.error(trace)
        raise HTTPException(status_code=500, detail=error_msg)


@app.get("/api/experiment-courses/{task_id}/rounds")
async def get_experiment_rounds(
    task_id: str,
    course_no: str = Query(..., description="课程号"),
    project_code: str = Query(..., description="实验项目代码"),
    term: str = Query(..., description="学年学期代码"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """获取实验班列表"""
    try:
        from neu_academic.experiment import ExperimentCourseAPI
        api = ExperimentCourseAPI(auth)
        
        rounds = api.get_rounds(term, task_id, course_no, project_code)
        
        return {
            "rounds": [
                {
                    "wid": r.wid,
                    "round_name": r.round_name,
                    "teacher": r.teacher,
                    "selected_count": r.selected_count,
                    "capacity": r.capacity,
                    "is_full": r.is_full,
                    "week": r.week,
                    "day": r.day,
                    "time": r.time,
                    "location": r.location,
                    "select_start": r.select_start,
                    "select_end": r.select_end,
                    "conflict": r.conflict,
                    "selected": r.selected,
                    "can_select": r.can_select,
                }
                for r in rounds
            ],
            "total": len(rounds),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取实验班失败: {str(e)}")


@app.post("/api/experiment-courses/select")
async def select_experiment_course(
    data: Dict[str, str],
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    选择实验班
    
    请求体:
    {
        "term": "2025-2026-2",
        "task_id": "...",
        "project_code": "...",
        "round_id": "..."
    }
    """
    try:
        from neu_academic.experiment import ExperimentCourseAPI
        api = ExperimentCourseAPI(auth)
        
        result = api.select(
            data["term"],
            data["task_id"],
            data["project_code"],
            data["round_id"]
        )
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"选课失败: {str(e)}")


@app.post("/api/experiment-courses/deselect")
async def deselect_experiment_course(
    data: Dict[str, str],
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    退选实验班
    
    请求体:
    {
        "term": "2025-2026-2",
        "task_id": "...",
        "project_code": "...",
        "round_id": "..."
    }
    """
    try:
        from neu_academic.experiment import ExperimentCourseAPI
        api = ExperimentCourseAPI(auth)
        
        result = api.deselect(
            data["term"],
            data["task_id"],
            data["project_code"],
            data["round_id"]
        )
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"退课失败: {str(e)}")


# ── 用户头像 API ───────────────────────────────────────────────────────────────

@app.get("/api/user/info")
async def get_user_info(auth: NEUAuthClient = Depends(require_auth)):
    """获取当前用户信息（包含头像）"""
    try:
        user_info = auth.get_user_info()
        if not user_info:
            raise HTTPException(status_code=500, detail="获取用户信息失败")
        return user_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取用户信息失败: {str(e)}")


@app.get("/api/user/avatar")
async def get_user_avatar(
    refresh: bool = Query(False, description="强制刷新头像"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取用户头像图片
    
    支持缓存：默认使用本地缓存，refresh=true 时强制从服务器获取
    """
    try:
        _api_logger.info(f"[Avatar] 开始获取头像, user={auth.username}, refresh={refresh}")
        
        # 首先获取用户信息（包含头像token）
        user_info = auth.get_user_info()
        
        if not user_info:
            _api_logger.warning("[Avatar] 获取用户信息失败")
            raise HTTPException(status_code=404, detail="获取用户信息失败")
        
        avatar_token = user_info.get('avatar_token')
        if not avatar_token:
            _api_logger.warning("[Avatar] 无头像token")
            raise HTTPException(status_code=404, detail="用户未上传头像")
        
        # 检查本地缓存
        if not refresh:
            cached_avatar = _storage.load_avatar()
            if cached_avatar and _storage.is_avatar_valid(auth.username, avatar_token):
                _api_logger.info(f"[Avatar] 使用本地缓存，大小: {len(cached_avatar)} bytes")
                return Response(content=cached_avatar, media_type="image/png")
        
        # 从服务器获取
        avatar_data = auth.get_avatar(avatar_token)
        if not avatar_data:
            _api_logger.warning("[Avatar] 获取头像数据失败")
            raise HTTPException(status_code=404, detail="头像不存在")
        
        # 保存到本地缓存
        try:
            _storage.save_avatar(avatar_data, auth.username, avatar_token)
            _api_logger.info(f"[Avatar] 已缓存头像，大小: {len(avatar_data)} bytes")
        except Exception as cache_error:
            _api_logger.warning(f"[Avatar] 缓存头像失败: {cache_error}")
        
        return Response(content=avatar_data, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_msg = f"[Avatar] 获取头像失败: {e}"
        trace = traceback.format_exc()
        _api_logger.error(error_msg)
        _api_logger.error(trace)
        raise HTTPException(status_code=500, detail=f"获取头像失败: {str(e)}")


# ── GPA模拟文件管理 API ─────────────────────────────────────────────────────────

import json
from pathlib import Path

# GPA模拟文件存储目录
def get_gpa_simulation_dir():
    """获取GPA模拟文件存储目录"""
    sim_dir = os.path.join(_storage.config.data_dir, "成绩")
    os.makedirs(sim_dir, exist_ok=True)
    return sim_dir


class GPASimulationExportRequest(BaseModel):
    """GPA模拟导出请求"""
    filename: str = Field(..., description="文件名")
    data: Dict[str, Any] = Field(..., description="模拟数据")


class GPASimulationFile(BaseModel):
    """GPA模拟文件信息"""
    filename: str
    size: int
    modified_time: str
    stats: Optional[Dict] = None


@app.post("/api/gpa-simulation/export")
async def export_gpa_simulation(
    request: GPASimulationExportRequest,
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    导出GPA模拟数据到data目录
    
    保存到 data/gpa_simulations/ 目录下
    """
    try:
        # 确保文件名安全
        safe_filename = os.path.basename(request.filename)
        if not safe_filename.endswith('.json'):
            safe_filename += '.json'
        
        filepath = os.path.join(get_gpa_simulation_dir(), safe_filename)
        
        # 添加导出元数据
        export_data = {
            **request.data,
            "export_info": {
                "exported_by": auth.username,
                "exported_at": datetime.now().isoformat(),
                "version": "1.0"
            }
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        
        _api_logger.info(f"[GPA-Sim] 导出成功: {safe_filename}, user={auth.username}")
        return {
            "success": True,
            "filename": safe_filename,
            "path": filepath
        }
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 导出失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@app.get("/api/gpa-simulation/files", response_model=List[GPASimulationFile])
async def list_gpa_simulation_files(auth: NEUAuthClient = Depends(require_auth)):
    """
    列出所有GPA模拟文件
    
    从 data/gpa_simulations/ 目录读取
    """
    try:
        files = []
        for filename in os.listdir(get_gpa_simulation_dir()):
            if filename.endswith('.json'):
                filepath = os.path.join(get_gpa_simulation_dir(), filename)
                stat = os.stat(filepath)
                
                # 尝试读取统计信息
                stats = None
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        stats = data.get('stats')
                except:
                    pass
                
                files.append({
                    "filename": filename,
                    "size": stat.st_size,
                    "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "stats": stats
                })
        
        # 按修改时间倒序
        files.sort(key=lambda x: x["modified_time"], reverse=True)
        return files
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 列出文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"列出文件失败: {str(e)}")


@app.get("/api/gpa-simulation/file/{filename}")
async def get_gpa_simulation_file(
    filename: str,
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取指定GPA模拟文件内容
    """
    try:
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(get_gpa_simulation_dir(), safe_filename)
        
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return data
    except HTTPException:
        raise
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 读取文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")


@app.delete("/api/gpa-simulation/file/{filename}")
async def delete_gpa_simulation_file(
    filename: str,
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    删除指定GPA模拟文件
    """
    try:
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(get_gpa_simulation_dir(), safe_filename)
        
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")
        
        os.remove(filepath)
        _api_logger.info(f"[GPA-Sim] 删除文件: {safe_filename}, user={auth.username}")
        return {"success": True, "message": "文件已删除"}
    except HTTPException:
        raise
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 删除文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")


# ── 启动 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    
    # 从环境变量读取端口，默认为 8000
    port = int(os.environ.get("PORT", os.environ.get("BACKEND_PORT", "8000")))
    host = os.environ.get("HOST", "0.0.0.0")
    
    print(f"启动 NEU 教务系统工具箱 API 服务...")
    print(f"监听地址: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/docs")
    
    uvicorn.run(app, host=host, port=port)
