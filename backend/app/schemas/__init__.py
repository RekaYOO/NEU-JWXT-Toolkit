"""
Pydantic 模型定义
按领域拆分为独立模块
"""

from .auth import LoginRequest, LoginResponse
from .scores import CourseScoreModel, TermScoresModel, ScoresResponse, ColumnConfig
from .logs import LogSummaryResponse, LogEntryResponse, LogListResponse
from .report import CreditSummaryModel, CourseModel, CategoryNodeModel, AcademicReportResponse
from .gpa import GPASimulationExportRequest, GPASimulationFile
from .evaluation import EvaluationSubmitRequest, EvaluationBatchRequest

__all__ = [
    "LoginRequest", "LoginResponse",
    "CourseScoreModel", "TermScoresModel", "ScoresResponse", "ColumnConfig",
    "LogSummaryResponse", "LogEntryResponse", "LogListResponse",
    "CreditSummaryModel", "CourseModel", "CategoryNodeModel", "AcademicReportResponse",
    "GPASimulationExportRequest", "GPASimulationFile",
    "EvaluationSubmitRequest", "EvaluationBatchRequest",
]
