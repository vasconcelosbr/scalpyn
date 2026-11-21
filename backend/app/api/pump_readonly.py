"""Isolated machine authentication; the key never authorizes a JWT/trading route."""
import hashlib
import hmac
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ..config import settings
from ..schemas.pump_readonly import PumpStrongResponse
from ..services.pump_readonly import PumpExportUnavailable, read_strong_assets
from ..services.radar_readonly import readonly_session

logger = logging.getLogger(__name__)


class PumpReadRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                response = await original(request)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            except RequestValidationError:
                response = JSONResponse({"detail": "Invalid request"}, status_code=422)
            except Exception:
                # Never log credentials, query text, response data or exception text.
                response = JSONResponse({"detail": "pump_export_unavailable"}, status_code=503)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Scalpyn-Read-Mode"] = "persisted-only"
            logger.info("service_principal=pump_readonly status=%s", response.status_code)
            return response

        return handler


router = APIRouter(prefix="/api/integrations/pump-monitor", tags=["Pump Monitor Readonly"],
                   route_class=PumpReadRoute)


def pump_owner(request: Request) -> UUID:
    expected = settings.PUMP_READONLY_KEY_SHA256.strip().lower()
    if not expected:
        raise HTTPException(404, "Not found")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise HTTPException(503, "Service configuration unavailable")
    keys = request.headers.getlist("x-scalpyn-service-key")
    if len(keys) != 1 or not keys[0] or not hmac.compare_digest(
        hashlib.sha256(keys[0].encode("utf-8")).hexdigest(), expected
    ):
        raise HTTPException(401, "Invalid or missing service key")
    if request.query_params:
        raise HTTPException(422, "This endpoint does not accept query parameters")
    try:
        return UUID(settings.PUMP_READONLY_USER_ID.strip())
    except ValueError:
        raise HTTPException(503, "Service configuration unavailable") from None


@router.get("/strong-rising/assets", response_model=PumpStrongResponse)
async def strong_rising_assets(owner: UUID = Depends(pump_owner)):
    try:
        async with readonly_session() as db:
            return await read_strong_assets(db, owner)
    except PumpExportUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
