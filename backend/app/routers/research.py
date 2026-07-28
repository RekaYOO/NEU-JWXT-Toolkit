from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.dependencies import require_auth
from backend.app.schemas.research import (
    ResearchCancellationRequest,
    ResearchEnrollmentRequest,
)
from backend.core.academic.research_training import (
    ResearchTrainingAPI,
    ResearchTrainingError,
)
from backend.core.auth import NEUAuthClient


router = APIRouter()


def _api(auth: NEUAuthClient) -> ResearchTrainingAPI:
    return ResearchTrainingAPI(auth)


@router.get("/research-training")
async def get_research_training(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    keyword: str = Query("", max_length=100),
    project_name: str = Query("", max_length=100),
    advisor_name: str = Query("", max_length=50),
    auth: NEUAuthClient = Depends(require_auth),
):
    try:
        api = _api(auth)
        batch = api.get_current_batch()
        eligibility = api.get_eligibility(batch.batch_id)
        topics = api.get_topics(
            batch.batch_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            project_name=project_name,
            advisor_name=advisor_name,
        )
        return {
            "batch": batch.__dict__,
            "eligibility": eligibility.__dict__,
            **topics,
        }
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"获取科研训练课题失败: {error}") from error


@router.get("/research-training/topics/{topic_id}")
async def get_research_topic(
    topic_id: str,
    auth: NEUAuthClient = Depends(require_auth),
):
    try:
        return _api(auth).get_topic_detail(topic_id)
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/research-training/confirmed")
async def get_confirmed_research_topics(
    auth: NEUAuthClient = Depends(require_auth),
):
    try:
        api = _api(auth)
        batch = api.get_current_batch()
        topics = api.get_confirmed_topics(batch.batch_id)
        return {"batch": batch.__dict__, "topics": topics, "total": len(topics)}
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/research-training/enroll")
async def enroll_research_topic(
    request: ResearchEnrollmentRequest,
    auth: NEUAuthClient = Depends(require_auth),
):
    try:
        return _api(auth).enroll(
            request.topic_id,
            batch_id=request.batch_id,
            phone=request.phone,
            email=request.email,
            reason=request.reason,
        )
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/research-training/cancel")
async def cancel_research_enrollment(
    request: ResearchCancellationRequest,
    auth: NEUAuthClient = Depends(require_auth),
):
    try:
        return _api(auth).cancel_enrollment(request.topic_id)
    except ResearchTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
