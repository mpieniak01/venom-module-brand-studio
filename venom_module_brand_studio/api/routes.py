from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from venom_module_brand_studio.api.schemas import (
    AuditResponse,
    CandidatesResponse,
    DraftBundle,
    DraftGenerateRequest,
    PublishRequest,
    PublishResult,
    QueueCreateResponse,
    QueueDraftRequest,
    QueueResponse,
)
from venom_module_brand_studio.services.service import (
    BrandStudioService,
    get_brand_studio_service,
    health_payload,
)

router = APIRouter(prefix="/api/v1/brand-studio", tags=["brand-studio"])


@router.get("/health")
async def health() -> dict[str, str]:
    return health_payload()


def _actor_from_headers(
    x_authenticated_user: Annotated[str | None, Header()] = None,
    x_user: Annotated[str | None, Header()] = None,
    x_admin_user: Annotated[str | None, Header()] = None,
) -> str:
    for candidate in (x_authenticated_user, x_user, x_admin_user):
        if candidate:
            return candidate
    return "unknown"


ServiceDep = Annotated[BrandStudioService, Depends(get_brand_studio_service)]
ActorDep = Annotated[str, Depends(_actor_from_headers)]


@router.get("/sources/candidates", response_model=CandidatesResponse)
async def list_candidates(
    service: ServiceDep,
    channel: Annotated[str | None, Query()] = None,
    lang: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> CandidatesResponse:
    items, refreshed_at = service.list_candidates(
        channel=channel,
        lang=lang,
        limit=limit,
        min_score=min_score,
    )
    return CandidatesResponse(count=len(items), items=items, refreshed_at=refreshed_at)


@router.post(
    "/drafts/generate",
    response_model=DraftBundle,
    responses={404: {"description": "Candidate not found"}},
)
async def generate_draft(
    payload: DraftGenerateRequest,
    service: ServiceDep,
    actor: ActorDep,
) -> DraftBundle:
    try:
        return service.generate_draft(
            candidate_id=payload.candidate_id,
            channels=payload.channels,
            languages=payload.languages,
            tone=payload.tone,
            actor=actor,
        )
    except KeyError as exc:
        if str(exc).strip("'") == "candidate_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found"
            ) from exc
        raise


@router.post(
    "/drafts/{draft_id}/queue",
    response_model=QueueCreateResponse,
    responses={404: {"description": "Draft not found"}},
)
async def queue_draft(
    draft_id: str,
    payload: QueueDraftRequest,
    service: ServiceDep,
    actor: ActorDep,
) -> QueueCreateResponse:
    try:
        item = service.queue_draft(
            draft_id=draft_id,
            target_channel=payload.target_channel,
            actor=actor,
        )
    except KeyError as exc:
        if str(exc).strip("'") == "draft_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft not found",
            ) from exc
        raise
    return QueueCreateResponse(
        item_id=item.item_id,
        status=item.status,
        created_at=item.created_at,
    )


@router.post(
    "/queue/{item_id}/publish",
    response_model=PublishResult,
    responses={
        400: {"description": "confirm_publish must be true"},
        404: {"description": "Queue item not found"},
    },
)
async def publish_queue_item(
    item_id: str,
    payload: PublishRequest,
    service: ServiceDep,
    actor: ActorDep,
) -> PublishResult:
    try:
        return service.publish_queue_item(
            item_id=item_id,
            confirm_publish=payload.confirm_publish,
            actor=actor,
        )
    except ValueError as exc:
        if str(exc) == "confirm_publish_required":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="confirm_publish must be true",
            ) from exc
        raise
    except KeyError as exc:
        if str(exc).strip("'") == "queue_item_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Queue item not found"
            ) from exc
        raise


@router.get("/queue", response_model=QueueResponse)
async def list_queue(service: ServiceDep) -> QueueResponse:
    items = service.queue_items()
    return QueueResponse(count=len(items), items=items)


@router.get("/audit", response_model=AuditResponse)
async def list_audit(service: ServiceDep) -> AuditResponse:
    items = service.audit_items()
    return AuditResponse(count=len(items), items=items)
