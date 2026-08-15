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
from .experiment import ExperimentCourseMutationRequest
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
from .academic_documents import AcademicDocumentGenerateRequest
from .course_selection import (
    CourseMarketModel,
    CourseMarketSnapshotModel,
    CourseSelectionOptimizeRequest,
    CourseSelectionOptimizeResponse,
    CourseSelectionPolicyModel,
    JwxkBatchModel, JwxkSettingsUpdate, JwxkStatusResponse,
    JwxkBatchRequest, JwxkCourseItem, JwxkCourseSearchRequest,
    JwxkCourseSearchResponse, JwxkSelectedResponse,
    JwxkBatchConfirmRequest, JwxkCourseSelectRequest,
    JwxkCourseDeselectRequest, JwxkMutationResponse,
    JwxkCatalogSearchRequest, JwxkCatalogSearchResponse,
    JwxkCatalogDetailRequest, JwxkCatalogDetailResponse,
    JwxkEligibilityRequest, JwxkEligibilityResponse,
    JwxkSelectionScheduleResponse, JwxkPlanPreviewRequest,
    JwxkPlanPreviewResponse, JwxkPlanGroup, JwxkSavedPlanRequest,
    JwxkWeightPlanRequest, JwxkWeightConfigResponse,
    JwxkAutomationTaskRequest, JwxkAutomationTaskAction,
)
from .timetable import (
    TimetableContextRequest,
    TimetableContextResponse,
    TimetableCourseModel,
    TimetableScheduleRequest,
    TimetableScheduleResponse,
    TimetableTargetSearchRequest,
    TimetableTargetSearchResponse,
    TimetableTermsResponse,
    PersonalTimetableResponse,
)
from .scheduling import (
    ScheduleCandidateConflictModel,
    ScheduleConflictBatchRequest,
    ScheduleConflictBatchResponse,
    ScheduleConflictMatchModel,
    ScheduleMeetingInput,
)
from .course_outline import (
    CourseOutlineSearchRequest,
    CourseOutlineDetailRequest,
    CourseOutlineSectionsRequest,
    CourseOutlineAttachmentRequest,
    CourseOutlineMetadataReadRequest,
    CourseOutlineMetadataSyncRequest,
)

__all__ = [
    "LoginRequest", "LoginResponse", "WebVPNQRStartRequest", "WebVPNQRStatusRequest",
    "WebVPNPasswordStartRequest", "WebVPNSMSCodeRequest", "WebVPNSMSVerifyRequest",
    "CourseScoreModel", "TermScoresModel", "ScoresResponse", "ColumnConfig",
    "ScoreDetailItem", "CourseScoreDetailResponse", "ScoreDetailQueryRequest",
    "LogSummaryResponse", "LogEntryResponse", "LogListResponse",
    "CreditSummaryModel", "CourseModel", "CategoryNodeModel", "AcademicReportResponse",
    "GPASimulationExportRequest", "GPASimulationFile",
    "EvaluationSubmitRequest", "EvaluationBatchRequest", "ExperimentCourseMutationRequest",
    "ExamTerm", "ExamItem", "ExamListResponse", "ExamTermsResponse",
    "ResearchEnrollmentRequest", "ResearchCancellationRequest",
    "ResearchFavoriteRequest", "ResearchFavoriteResponse",
    "ResearchCacheResponse", "ResearchChangeSummary",
    "CertificateArchiveRequest", "FestivalActivitiesResponse", "FestivalActivityModel",
    "CourseMarketModel", "CourseMarketSnapshotModel",
    "CourseSelectionOptimizeRequest", "CourseSelectionOptimizeResponse",
    "CourseSelectionPolicyModel",
    "JwxkBatchModel", "JwxkSettingsUpdate", "JwxkStatusResponse",
    "JwxkBatchRequest", "JwxkCourseItem", "JwxkCourseSearchRequest",
    "JwxkCourseSearchResponse", "JwxkSelectedResponse",
    "JwxkBatchConfirmRequest", "JwxkCourseSelectRequest",
    "JwxkCourseDeselectRequest", "JwxkMutationResponse",
    "JwxkCatalogSearchRequest", "JwxkCatalogSearchResponse",
    "JwxkCatalogDetailRequest", "JwxkCatalogDetailResponse",
    "JwxkEligibilityRequest", "JwxkEligibilityResponse",
    "JwxkSelectionScheduleResponse", "JwxkPlanPreviewRequest",
    "JwxkPlanPreviewResponse", "JwxkPlanGroup", "JwxkSavedPlanRequest",
    "JwxkWeightPlanRequest", "JwxkWeightConfigResponse",
    "JwxkAutomationTaskRequest", "JwxkAutomationTaskAction",
    "TimetableContextRequest", "TimetableContextResponse",
    "TimetableCourseModel", "TimetableScheduleRequest", "TimetableScheduleResponse",
    "TimetableTargetSearchRequest", "TimetableTargetSearchResponse",
    "TimetableTermsResponse",
    "PersonalTimetableResponse",
    "ScheduleMeetingInput", "ScheduleConflictBatchRequest",
    "ScheduleConflictMatchModel", "ScheduleCandidateConflictModel",
    "ScheduleConflictBatchResponse",
    "CourseOutlineSearchRequest", "CourseOutlineDetailRequest",
    "CourseOutlineSectionsRequest", "CourseOutlineAttachmentRequest",
    "CourseOutlineMetadataReadRequest", "CourseOutlineMetadataSyncRequest",
]
from .tracking import GradeTrackingConfigUpdate
from .system_settings import CacheResourceSetting, CacheSettingsUpdate, SystemSettingsResponse
