from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from venom_module_brand_studio.api.schemas import (
    AuditResponse,
    CandidatesResponse,
    ChannelAccountCreateRequest,
    ChannelAccountResponse,
    ChannelAccountsResponse,
    ChannelAccountTestResponse,
    ChannelAccountUpdateRequest,
    ChannelId,
    ChannelsResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    DraftBundle,
    DraftGenerateRequest,
    IntegrationId,
    IntegrationsResponse,
    IntegrationTestResponse,
    PublishRequest,
    PublishResult,
    QueueCreateResponse,
    QueueDraftRequest,
    QueueResponse,
    RefreshResponse,
    StrategiesResponse,
    StrategyCreateRequest,
    StrategyResponse,
    StrategyUpdateRequest,
)
from venom_module_brand_studio.services.service import (
    BrandStudioService,
    ChannelAccountNotFoundError,
    LastStrategyDeletionError,
    StrategyNotFoundError,
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


def _autonomy_guard(
    x_autonomy_level: Annotated[int | None, Header(alias="X-Autonomy-Level")] = None,
) -> None:
    """
    Module uses core autonomy as the single source of truth.
    No independent permission model is introduced in module layer.
    """
    required_level_raw = (os.getenv("BRAND_STUDIO_REQUIRED_AUTONOMY_LEVEL") or "20").strip()
    try:
        required_level = int(required_level_raw)
    except ValueError:
        required_level = 20

    # Preferred path: shared core PermissionGuard from Venom runtime.
    try:
        from venom_core.core.permission_guard import permission_guard  # type: ignore

        current_level = int(permission_guard.get_current_level())
        if current_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Insufficient autonomy level for Brand Studio operation "
                    f"(current={current_level}, required={required_level})"
                ),
            )
        return
    except HTTPException:
        raise
    except (ModuleNotFoundError, ImportError, AttributeError):
        # Fallback for external module tests/sandbox:
        # host must forward autonomy level via header; no implicit bypass.
        if x_autonomy_level is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing autonomy context header (X-Autonomy-Level)",
            )
        if x_autonomy_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Insufficient autonomy level for Brand Studio operation "
                    f"(current={x_autonomy_level}, required={required_level})"
                ),
            )


ServiceDep = Annotated[BrandStudioService, Depends(get_brand_studio_service)]
FeatureDep = Annotated[None, Depends(_feature_guard)]
ActorDep = Annotated[str, Depends(_actor_required)]
OptionalActorDep = Annotated[str, Depends(_actor_optional)]
AutonomyDep = Annotated[None, Depends(_autonomy_guard)]


@router.get("/sources/candidates", response_model=CandidatesResponse)
async def list_candidates(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
    channel: Annotated[str | None, Query()] = None,
    lang: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    min_score: Annotated[
        float | None,
        Query(
            ge=0.0,
            le=1.0,
            description=(
                "Minimum relevance score. If omitted/null, active strategy default is used. "
                "Set explicit value (e.g. 0.0) to override strategy default."
            ),
        ),
    ] = None,
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
    _autonomy: AutonomyDep,
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
    _autonomy: AutonomyDep,
) -> QueueCreateResponse:
    try:
        item = service.queue_draft(
            draft_id=draft_id,
            target_channel=payload.target_channel,
            account_id=payload.account_id,
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
    _autonomy: AutonomyDep,
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


@router.get("/config", response_model=ConfigResponse)
async def get_config(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
) -> ConfigResponse:
    active_strategy_id, active_strategy = service.config()
    return ConfigResponse(active_strategy_id=active_strategy_id, active_strategy=active_strategy)


@router.put("/config", response_model=ConfigResponse)
async def update_config(
    payload: ConfigUpdateRequest,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> ConfigResponse:
    strategy = service.update_active_config(payload, actor=actor)
    active_strategy_id, _ = service.config()
    return ConfigResponse(active_strategy_id=active_strategy_id, active_strategy=strategy)


@router.post("/config/refresh", response_model=RefreshResponse)
async def refresh_config(
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> RefreshResponse:
    refreshed_at, count = service.force_refresh(actor=actor)
    return RefreshResponse(refreshed_at=refreshed_at, count=count)


@router.get("/strategies", response_model=StrategiesResponse)
async def list_strategies(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
) -> StrategiesResponse:
    active_strategy_id, items = service.strategies()
    return StrategiesResponse(active_strategy_id=active_strategy_id, items=items)


@router.post(
    "/strategies",
    response_model=StrategyResponse,
    responses={404: {"description": "Base strategy not found"}},
)
async def create_strategy(
    payload: StrategyCreateRequest,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> StrategyResponse:
    try:
        item = service.create_strategy(payload, actor=actor)
    except StrategyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Base strategy not found",
        ) from exc
    active_strategy_id, _ = service.strategies()
    return StrategyResponse(item=item, active_strategy_id=active_strategy_id)


@router.put(
    "/strategies/{strategy_id}",
    response_model=StrategyResponse,
    responses={404: {"description": "Strategy not found"}},
)
async def update_strategy(
    strategy_id: str,
    payload: StrategyUpdateRequest,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> StrategyResponse:
    try:
        item = service.update_strategy(strategy_id, payload, actor=actor)
    except StrategyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        ) from exc
    active_strategy_id, _ = service.strategies()
    return StrategyResponse(item=item, active_strategy_id=active_strategy_id)


@router.delete(
    "/strategies/{strategy_id}",
    responses={
        204: {"description": "Strategy deleted"},
        400: {"description": "Cannot delete last strategy"},
        404: {"description": "Strategy not found"},
    },
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_strategy(
    strategy_id: str,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> None:
    try:
        service.delete_strategy(strategy_id, actor=actor)
    except StrategyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        ) from exc
    except LastStrategyDeletionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last strategy cannot be deleted",
        ) from exc


@router.post(
    "/strategies/{strategy_id}/activate",
    response_model=ConfigResponse,
    responses={404: {"description": "Strategy not found"}},
)
async def activate_strategy(
    strategy_id: str,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> ConfigResponse:
    try:
        active = service.activate_strategy(strategy_id, actor=actor)
    except StrategyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        ) from exc
    return ConfigResponse(active_strategy_id=active.id, active_strategy=active)


@router.get("/integrations", response_model=IntegrationsResponse)
async def list_integrations(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
) -> IntegrationsResponse:
    return IntegrationsResponse(items=service.integrations())


@router.post(
    "/integrations/{integration_id}/test",
    response_model=IntegrationTestResponse,
)
async def test_integration(
    integration_id: IntegrationId,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> IntegrationTestResponse:
    return service.test_integration(integration_id=integration_id, actor=actor)


@router.get("/channels", response_model=ChannelsResponse)
async def list_channels(
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
) -> ChannelsResponse:
    return service.channels()


@router.get("/channels/{channel}/accounts", response_model=ChannelAccountsResponse)
async def list_channel_accounts(
    channel: ChannelId,
    _feature: FeatureDep,
    service: ServiceDep,
    _actor: OptionalActorDep,
) -> ChannelAccountsResponse:
    return service.channel_accounts(channel)


@router.post("/channels/{channel}/accounts", response_model=ChannelAccountResponse)
async def create_channel_account(
    channel: ChannelId,
    payload: ChannelAccountCreateRequest,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> ChannelAccountResponse:
    item = service.create_channel_account(channel, payload, actor=actor)
    return ChannelAccountResponse(item=item)


@router.put(
    "/channels/{channel}/accounts/{account_id}",
    response_model=ChannelAccountResponse,
    responses={404: {"description": "Account not found"}},
)
async def update_channel_account(
    channel: ChannelId,
    account_id: str,
    payload: ChannelAccountUpdateRequest,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> ChannelAccountResponse:
    try:
        item = service.update_channel_account(channel, account_id, payload, actor=actor)
    except ChannelAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        ) from exc
    return ChannelAccountResponse(item=item)


@router.delete(
    "/channels/{channel}/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Account not found"}},
)
async def delete_channel_account(
    channel: ChannelId,
    account_id: str,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> None:
    try:
        service.delete_channel_account(channel, account_id, actor=actor)
    except ChannelAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        ) from exc


@router.post(
    "/channels/{channel}/accounts/{account_id}/activate",
    response_model=ChannelAccountResponse,
    responses={404: {"description": "Account not found"}},
)
async def activate_channel_account(
    channel: ChannelId,
    account_id: str,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> ChannelAccountResponse:
    try:
        item = service.activate_channel_account(channel, account_id, actor=actor)
    except ChannelAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        ) from exc
    return ChannelAccountResponse(item=item)


@router.post(
    "/channels/{channel}/accounts/{account_id}/test",
    response_model=ChannelAccountTestResponse,
    responses={404: {"description": "Account not found"}},
)
async def test_channel_account(
    channel: ChannelId,
    account_id: str,
    _feature: FeatureDep,
    service: ServiceDep,
    actor: ActorDep,
    _autonomy: AutonomyDep,
) -> ChannelAccountTestResponse:
    try:
        return service.test_channel_account(channel, account_id, actor=actor)
    except ChannelAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        ) from exc
