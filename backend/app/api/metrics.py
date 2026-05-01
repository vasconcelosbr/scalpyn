"""Prometheus ``/metrics`` endpoint."""

from fastapi import APIRouter, Response

from ..services.robust_indicators.metrics import render_metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
