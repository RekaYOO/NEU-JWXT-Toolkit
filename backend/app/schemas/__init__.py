"""
Pydantic 模型定义
按领域拆分为独立模块
"""

from .auth import (
    LoginRequest, LoginResponse, WebVPNQRStartRequest, WebVPNQRStatusRequest,
    WebVPNPasswordStartRequest, WebVPNSMSCodeRequest, WebVPNSMSVerifyRequest,
)
from .scores import (
    ColumnConfig,
    CourseScoreDetailResponse,
    CourseScoreModel,
    ScoreDetailItem,
    ScoreDetailQueryRequest,
    ScoresResponse,
    TermScoresModel,
)
from .logs import LogSummaryResponse, LogEntryResponse, LogListResponse
from .report import CreditSummaryModel, CourseModel, CategoryNodeModel, AcademicReportResponse
from .gpa import GPASimulationExportRequest, GPASimulationFile
from .evaluation import EvaluationSubmitRequest, EvaluationBatchRequest
from .exam import ExamTerm, ExamItem, ExamListResponse, ExamTermsResponse
from .research import (
    ResearchCancellationRequest,
    ResearchCacheResponse,
    ResearchChangeSummary,
    ResearchEnrollmentRequest,
    ResearchFavoriteRequest,
    ResearchFavoriteResponse,
)
from .festival_activities import (
    CertificateArchiveRequest,
    FestivalActivitiesResponse,
    FestivalActivityModel,
)

__all__ = [
    "LoginRequest", "LoginResponse", "WebVPNQRStartRequest", "WebVPNQRStatusRequest",
    "WebVPNPasswordStartRequest", "WebVPNSMSCodeRequest", "WebVPNSMSVerifyRequest",
    "CourseScoreModel", "TermScoresModel", "ScoresResponse", "ColumnConfig",
    "ScoreDetailItem", "CourseScoreDetailResponse", "ScoreDetailQueryRequest",
    "LogSummaryResponse", "LogEntryResponse", "LogListResponse",
    "CreditSummaryModel", "CourseModel", "CategoryNodeModel", "AcademicReportResponse",
    "GPASimulationExportRequest", "GPASimulationFile",
    "EvaluationSubmitRequest", "EvaluationBatchRequest",
    "ExamTerm", "ExamItem", "ExamListResponse", "ExamTermsResponse",
    "ResearchEnrollmentRequest", "ResearchCancellationRequest",
    "ResearchFavoriteRequest", "ResearchFavoriteResponse",
    "ResearchCacheResponse", "ResearchChangeSummary",
    "CertificateArchiveRequest", "FestivalActivitiesResponse", "FestivalActivityModel",
]
from .tracking import GradeTrackingConfigUpdate
