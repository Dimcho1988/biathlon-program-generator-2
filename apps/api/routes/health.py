"""Health HTTP routes."""

from hrmod_lab.schemas import MODEL_VERSION as HRMOD_MODEL_VERSION
from vflat_b65 import MODEL_VERSION as VFLAT_MODEL_VERSION
from vflat_b65 import SPRINT_STR_MODEL_VERSION
from ..schemas import HealthResponse, ModelHealthResponse, TrainingStatusResponse
from ..training_status import build_demo_training_status
from .. import model_service
from fastapi import APIRouter

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Keep Render liveness independent from the synchronous worker pool."""

    return HealthResponse(status="ok")


@router.get("/health/models", response_model=ModelHealthResponse)
async def model_health() -> ModelHealthResponse:
    """Expose non-sensitive shadow versions for deployment verification."""

    from ..shadow_models.hrmod_v4 import SOURCE_COMMIT

    return ModelHealthResponse(
        status="ok",
        vflat_model_version=VFLAT_MODEL_VERSION,
        sprint_str_model_version=SPRINT_STR_MODEL_VERSION,
        hrmod_model_version=HRMOD_MODEL_VERSION,
        hrmod_source_commit=SOURCE_COMMIT,
        recovery_model_version=model_service.recovery_v2.VERSION if model_service.enabled() else "main-load-recovery-v1",
        speed_model_version=model_service.speed_duration.VERSION,
    )


@router.get("/api/v1/demo/training-status", response_model=TrainingStatusResponse)
def demo_training_status() -> TrainingStatusResponse:
    return build_demo_training_status()
