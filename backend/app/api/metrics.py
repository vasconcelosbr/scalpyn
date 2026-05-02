"""Prometheus ``/metrics`` endpoint.

Access control (Task #167) — two layers, both required
------------------------------------------------------

``/metrics`` exposes live trading-engine telemetry (per-symbol confidence,
rejection reasons, exchange error rates, latency histograms) and must not
be reachable from the open internet.

1. **Network perimeter (primary).** ``cloudbuild.yaml`` deploys the
   Cloud Run service with ``--ingress=internal-and-cloud-load-balancing``,
   so the raw ``*.run.app`` URL returns 403 from Google's front end before
   a request ever reaches this handler. Frontend traffic continues to
   flow through the Vercel catch-all proxy
   (``frontend/app/api/[...path]/route.ts``) by re-pointing its
   ``BACKEND_URL`` env var at the Cloud Load Balancer hostname that
   fronts the service; Prometheus scrapes from inside the same VPC
   connector or via the LB allow-list (see ``docs/grafana/README.md``).

2. **Application-level bearer token (defense in depth).** Even if the
   ingress perimeter is ever loosened, this handler also enforces a
   shared bearer token, configured via the ``PROMETHEUS_BEARER_TOKEN``
   env var (mounted from the ``prometheus-bearer-token`` Secret Manager
   secret in ``cloudbuild.yaml``). Prometheus consumes it natively via
   ``bearer_token_file`` in its scrape config — no sidecar required.

Behavior:

* ``PROMETHEUS_BEARER_TOKEN`` env var **unset** → endpoint returns 404 so
  the route is invisible to scanners. This is the safe default: the
  service ships with metrics hidden until an operator explicitly opts in.
* Env var **set**, request missing/wrong ``Authorization: Bearer …`` →
  401 with ``WWW-Authenticate: Bearer``.
* Env var **set**, request carries the matching token → 200 with the
  Prometheus exposition body.
"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Response, status

from ..services.robust_indicators.metrics import render_metrics

router = APIRouter()


_BEARER_PREFIX = "Bearer "


def _expected_token() -> str | None:
    """Return the configured bearer token, or ``None`` if metrics are disabled."""
    token = os.environ.get("PROMETHEUS_BEARER_TOKEN", "").strip()
    return token or None


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    if not authorization.startswith(_BEARER_PREFIX):
        return None
    return authorization[len(_BEARER_PREFIX):].strip() or None


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(
    authorization: str | None = Header(default=None),
) -> Response:
    expected = _expected_token()
    if expected is None:
        # Metrics gate disabled → hide the endpoint entirely.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    presented = _extract_bearer(authorization)
    # ``hmac.compare_digest`` to avoid timing side-channels on the token.
    if presented is None or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
