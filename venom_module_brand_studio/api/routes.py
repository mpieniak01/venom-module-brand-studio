from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/brand-studio", tags=["brand-studio"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "module": "brand_studio"}
