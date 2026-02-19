from fastapi import APIRouter

from venom_module_brand_studio.services.service import health_payload

router = APIRouter(prefix="/api/v1/brand-studio", tags=["brand-studio"])


@router.get("/health")
async def health() -> dict[str, str]:
    return health_payload()
