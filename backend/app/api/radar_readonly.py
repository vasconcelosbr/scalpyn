"""Machine-only export; never invokes the interactive watchlist handler."""
import hashlib
import hmac
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from ..config import settings
from ..schemas.radar_readonly import RadarWatchlistResponse
from ..services.radar_readonly import read_watchlist, readonly_session

logger = logging.getLogger(__name__)
CACHE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Scalpyn-Read-Mode": "persisted-only",
}


class RadarReadRoute(APIRoute):
    """Apply cache policy and sanitized audit logs to successes and errors."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                response = await original(request)
            except HTTPException as exc:
                response = JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code,
                    headers=exc.headers,
                )
            except RequestValidationError:
                response = JSONResponse({"detail": "Invalid request"}, status_code=422)
            except Exception:
                # Deliberately omit exception text/traceback (may contain SQL/data).
                response = JSONResponse({"detail": "Radar export unavailable"}, status_code=503)
            response.headers.update(CACHE_HEADERS)
            try:
                identifier = str(UUID(str(request.path_params.get("watchlist_id"))))
            except ValueError:
                identifier = "invalid"
            logger.info("service_principal=radar_readonly watchlist_id=%s status=%s",
                        identifier, response.status_code)
            return response
        return handler


router = APIRouter(prefix="/api/integrations/radar", tags=["Radar Readonly"],
                   route_class=RadarReadRoute)


def radar_owner(request: Request, watchlist_id: UUID) -> UUID:
    expected = settings.RADAR_READONLY_KEY_SHA256.strip().lower()
    if not expected:
        raise HTTPException(404, "Not found")
    if request.method != "GET":
        raise HTTPException(405, "Method not allowed")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise HTTPException(500, "Service configuration unavailable")
    keys = request.headers.getlist("x-scalpyn-service-key")
    if len(keys) != 1 or not keys[0] or not hmac.compare_digest(
        hashlib.sha256(keys[0].encode("utf-8")).hexdigest(), expected
    ):
        raise HTTPException(401, "Invalid or missing service key")
    try:
        owner = UUID(settings.RADAR_READONLY_USER_ID.strip())
        entries = settings.RADAR_READONLY_WATCHLIST_IDS.split(",")
        allowed = {UUID(entry.strip()) for entry in entries}
    except ValueError:
        raise HTTPException(500, "Service configuration unavailable") from None
    if watchlist_id not in allowed:
        raise HTTPException(404, "Not found")
    return owner


@router.get("/watchlists/{watchlist_id}/assets", response_model=RadarWatchlistResponse)
async def radar_assets(watchlist_id: UUID, owner: UUID = Depends(radar_owner)):
    # Auth completes before opening a connection. This dependency is not shared
    # with any JWT route and accepts no JWT fallback.
    try:
        async with readonly_session() as db:
            result = await read_watchlist(db, watchlist_id, owner)
            if result is None:
                raise HTTPException(404, "Not found")
            return result
    except SQLAlchemyError:
        raise HTTPException(503, "Radar export unavailable") from None
