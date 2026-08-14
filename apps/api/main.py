"""FastAPI entry point for the first onFlows read-only API."""

from fastapi import FastAPI

from .schemas import HealthResponse, TrainingStatusResponse
from .training_status import build_demo_training_status

app = FastAPI(title="onFlows API", version="1.0.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/v1/demo/training-status", response_model=TrainingStatusResponse)
def demo_training_status() -> TrainingStatusResponse:
    return build_demo_training_status()
