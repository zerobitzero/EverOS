"""Health-check endpoints.

The controller exposes three K8s-style endpoints so liveness and readiness
can be distinguished cleanly:

- ``/livez``    — process heartbeat. Always 200. Use this for K8s liveness
                  probes. A failure means the process should be restarted.
- ``/readyz``   — process plus downstream connectivity. 200 if every probe
                  in :mod:`probes.default_probes` succeeds, 503 otherwise.
                  Use this for K8s readiness probes and load-balancer
                  drain checks. A failure means traffic should be steered
                  away until dependencies recover.
- ``/health``   — legacy alias kept for backward compatibility. Behaves
                  like ``/livez``.

The per-dependency status is also published as the
``evercore_dependency_healthy{name="..."}`` Prometheus gauge, so alerts
can fire without polling these endpoints from outside.
"""

from typing import Any, Dict

from fastapi.responses import JSONResponse

from common_utils.datetime_utils import get_now_with_timezone
from core.di.decorators import component
from core.interface.controller.base_controller import BaseController, get
from core.observation.logger import get_logger

from .probes import default_probes, run_all

logger = get_logger(__name__)


@component(name="healthController")
class HealthController(BaseController):
    """Liveness, readiness, and legacy health endpoints."""

    # Probe timeout: short enough that K8s probe budgets aren't blown,
    # long enough that a healthy backend on a transient hiccup still wins.
    READINESS_PROBE_TIMEOUT_S = 2.0

    def __init__(self) -> None:
        super().__init__(
            prefix="",  # endpoints live at the application root
            tags=["Health"],
            default_auth="none",  # probes must not require auth
        )

    @get("/livez", summary="Liveness probe", description="Process is alive (no downstream checks).")
    def livez(self) -> Dict[str, Any]:
        """Cheap heartbeat. Always returns 200 if the event loop is running."""
        return {
            "status": "alive",
            "timestamp": get_now_with_timezone().isoformat(),
        }

    @get(
        "/readyz",
        summary="Readiness probe",
        description="200 only when every downstream dependency is reachable.",
    )
    async def readyz(self) -> JSONResponse:
        """Run every dependency probe and aggregate the verdict.

        Returns 200 with the per-dependency breakdown when all probes pass.
        Returns 503 with the *same* JSON shape when any probe fails, so
        clients can parse a single schema for both outcomes.

        We return a ``JSONResponse`` directly rather than raising
        ``HTTPException``; the project's global exception handler reshapes
        HTTPException detail through ``ErrorApiResponse`` (which requires
        ``message: str``), which would corrupt the structured payload.
        """
        results = await run_all(
            default_probes(), timeout=self.READINESS_PROBE_TIMEOUT_S
        )
        all_healthy = all(r.healthy for r in results)
        payload: Dict[str, Any] = {
            "status": "ready" if all_healthy else "not_ready",
            "timestamp": get_now_with_timezone().isoformat(),
            "dependencies": [r.to_dict() for r in results],
        }
        # 503 Service Unavailable is the standard "alive but not serving
        # traffic" signal that K8s readiness probes expect.
        status_code = 200 if all_healthy else 503
        return JSONResponse(content=payload, status_code=status_code)

    @get(
        "/health",
        summary="Legacy health check",
        description="Deprecated alias for /livez. Kept for backward compatibility.",
    )
    def health(self) -> Dict[str, Any]:
        """Alias for ``/livez`` so existing clients keep working."""
        return self.livez()
