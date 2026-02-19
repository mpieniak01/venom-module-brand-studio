from __future__ import annotations

import os
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


def _feature_guard() -> None:
    enabled = (os.getenv("FEATURE_BRAND_STUDIO") or "").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Brand Studio feature disabled",
        )


def _allowed_users_guard(actor: str) -> None:
    raw = (os.getenv("BRAND_STUDIO_ALLOWED_USERS") or "").strip()
    if not raw:
        return
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    if actor not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not allowed for Brand Studio",
        )


def _actor_required(actor: str = Depends(_actor_from_headers)) -> str:
    if actor == "unknown":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authenticated user header",
        )
    _allowed_users_guard(actor)
    return actor


def _actor_optional(actor: str = Depends(_actor_from_headers)) -> str:
    if actor != "unknown":
        _allowed_users_guard(actor)
    return actor


ServiceDep = Annotated[BrandStudioService, Depends(get_brand_studio_service)]
FeatureDep = Annotated[None, Depends(_feature_guard)]
ActorDep = Annotated[str, Depends(_actor_required)]
OptionalActorDep = Annotated[str, Depends(_actor_optional)]


@router.get("/sources/candidates", response_model=CandidatesResponse)
async def list_candidates(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
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
    _feature: FeatureDep,
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
    responses={404: {"description": "Draft or variant not found"}},
)
async def queue_draft(
    draft_id: str,
    payload: QueueDraftRequest,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
) -> QueueCreateResponse:
    try:
        item = service.queue_draft(
            draft_id=draft_id,
            target_channel=payload.target_channel,
            target_language=payload.target_language,
            target_repo=payload.target_repo,
            target_path=payload.target_path,
            payload_override=payload.payload_override,
            actor=actor,
        )
    except KeyError as exc:
        if str(exc).strip("'") in {"draft_not_found", "draft_variant_not_found"}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft or variant not found",
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
        409: {"description": "Queue item is already published"},
    },
)
async def publish_queue_item(
    item_id: str,
    payload: PublishRequest,
    _feature: FeatureDep,
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
        if str(exc) == "queue_item_already_published":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Queue item already published",
            ) from exc
        raise
    except KeyError as exc:
        if str(exc).strip("'") == "queue_item_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Queue item not found"
            ) from exc
        raise


@router.get("/queue", response_model=QueueResponse)
async def list_queue(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
) -> QueueResponse:
    items = service.queue_items()
    return QueueResponse(count=len(items), items=items)


@router.get("/audit", response_model=AuditResponse)
async def list_audit(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
) -> AuditResponse:
    items = service.audit_items()
    return AuditResponse(count=len(items), items=items)
