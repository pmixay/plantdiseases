"""Service metadata: health, capabilities, metrics, client telemetry.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response as StarletteResponse


def register(router: APIRouter, m) -> None:
    @router.get("/health")
    async def health_check():
        """Health check — server status, uptime, request count."""
        if m.pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not ready")
        uptime = time.time() - m.SERVER_START_TIME if m.SERVER_START_TIME else 0
        return {
            "status": "ok",
            "version": m.APP_VERSION,
            "num_classes": len(m.pipeline.classifier.CLASS_NAMES),
            "uptime_seconds": round(uptime, 1),
            "total_requests": m._snapshot_metrics()["total_requests"],
            "account_feature_expected": m.ACCOUNT_FEATURE_EXPECTED,
            "account_auth_configured": bool(
                getattr(m.account_authenticator, "configured", False)
            ),
        }


    @router.get("/capabilities")
    async def capabilities():
        """Model-family availability for the scan-screen picker.

        The client calls this once when opening the picker to decide which
        tiers to offer. It greys out / blocks any family reported unavailable —
        notably ``frontier`` when ``FRONTIER_ENABLED`` is false or its bundle
        failed to load. ``aqua`` is always offered because it runs on-device
        (its real capability depends on the app shipping the tflite student).
        Tolerates a not-yet-ready pipeline so the app can call it during boot.
        """
        base_ready = m._family_available("gamma")
        families = [
            {
                "id": "aqua",
                "available": True,
                "location": "on_device",
                "model_family": "aqua",
            },
            {
                "id": "gamma",
                "available": base_ready,
                "location": "server",
                "model_family": m.pipeline.classifier.model_family if base_ready else "gamma",
            },
            {
                "id": "frontier",
                "available": m._family_available("frontier"),
                "location": "server",
                "model_family": (
                    m.pipeline_frontier.classifier.model_family
                    if m.pipeline_frontier is not None else "frontier"
                ),
            },
        ]
        # Consolidated model/version metadata (formerly /version and /classes).
        # The int8 ship flag tells operators whether classifier_int8.onnx is genuine
        # INT8 or the FP32 ONNX renamed; defaults to false when the card is missing
        # or malformed. A notebook bundle (§6b) keeps the card under analysis/; older
        # trees had it at the root.
        int8_shipped = False
        int8_artifact: str | None = None
        for _card in (m.MODELS_DIR / "model_card.json", m.MODELS_DIR / "analysis" / "model_card.json"):
            try:
                with open(_card, encoding="utf-8") as fh:
                    _int8 = (json.load(fh).get("int8") or {})
            except (OSError, ValueError, AttributeError):
                continue
            int8_shipped = bool(_int8.get("shipped", False))
            _artifact = _int8.get("shipped_artifact")
            if isinstance(_artifact, str):
                int8_artifact = _artifact
            break

        model: dict | None = None
        model_version: str | None = None
        classes_list: list[dict] = []
        if base_ready:
            clf = m.pipeline.classifier
            model_version = getattr(clf, "meta", {}).get("model_version")
            model = {
                "classifier_name": "plantvision",
                "model_family": clf.model_family,
                "detector_arch": "YOLOv8n (single-class plant_leaf)",
                "classifier_arch": (
                    f"PlantVision {clf.architecture} @ "
                    f"{clf.img_size} px / {clf.backend} backend"
                ),
                "stages": 2,
                "focus": "houseplants + outdoor/garden ornamentals",
                "int8_shipped": int8_shipped,
                "int8_artifact": int8_artifact,
            }
            for name in clf.CLASS_NAMES:
                info = m.DISEASES_DATABASE.get(name, {})
                classes_list.append({
                    "class": name,
                    "name_en": info.get("name_en", name.replace("_", " ").title()),
                    "name_ru": info.get("name_ru", name),
                    "is_healthy": bool(info.get("is_healthy", False)),
                })

        return {
            "default": m.DEFAULT_FAMILY,
            "families": families,
            "account": {
                "expected": m.ACCOUNT_FEATURE_EXPECTED,
                "available": bool(getattr(m.account_authenticator, "configured", False)),
            },
            "version": m.APP_VERSION,
            "server_version": m.APP_VERSION,
            "model_version": model_version,
            "model": model,
            "classes": classes_list,
        }


    @router.get("/metrics")
    async def metrics():
        """Operational metrics (JSON). Useful for dashboards and jury demos."""
        snap = m._snapshot_metrics()
        uptime = time.time() - m.SERVER_START_TIME if m.SERVER_START_TIME else 0
        # The two store snapshots run full-table scans, so they come from a
        # short-lived shared snapshot; the in-process counters above are just
        # dict reads.
        training_snapshot, community_snapshot = await m._metrics_store_snapshots()
        return {
            "uptime_seconds": round(uptime, 1),
            **snap,
            "ai_doctor": m.chat_metrics_snapshot(),
            # Across every OpenRouter caller, not just the doctor — the number that
            # answers "is prompt caching engaging at all" after a model switch.
            "prompt_cache": m.prompt_cache_metrics_snapshot(),
            "care_plan_polish": m.care_plan_metrics_snapshot(),
            "plantnet": m.plantnet_metrics_snapshot(),
            "training": training_snapshot,
            "community": community_snapshot,
        }


    @router.get("/metrics/prometheus")
    async def metrics_prometheus():
        """Prometheus text exposition (v0.0.4) of the same counters as /api/metrics."""
        snap = m._snapshot_metrics()
        plantnet = m.plantnet_metrics_snapshot()
        uptime = time.time() - m.SERVER_START_TIME if m.SERVER_START_TIME else 0
        version = m._prom_label(m.APP_VERSION)

        lines = [
            "# HELP plantdiseases_uptime_seconds Seconds since the server started.",
            "# TYPE plantdiseases_uptime_seconds gauge",
            f"plantdiseases_uptime_seconds {uptime:.3f}",
            "# HELP plantdiseases_requests_total Total HTTP requests handled.",
            "# TYPE plantdiseases_requests_total counter",
            f"plantdiseases_requests_total {snap['total_requests']}",
            "# HELP plantdiseases_analyses_total Total successful /api/analyze responses.",
            "# TYPE plantdiseases_analyses_total counter",
            f"plantdiseases_analyses_total {snap['total_analyses']}",
            "# HELP plantdiseases_errors_total Total error responses from /api/analyze.",
            "# TYPE plantdiseases_errors_total counter",
            f"plantdiseases_errors_total {snap['total_errors']}",
            "# HELP plantdiseases_inference_inflight Inference requests holding an admission slot.",
            "# TYPE plantdiseases_inference_inflight gauge",
            f"plantdiseases_inference_inflight {snap['inference_inflight']}",
            "# HELP plantdiseases_inference_busy_rejections_total Inference requests turned away with 503 (server full).",
            "# TYPE plantdiseases_inference_busy_rejections_total counter",
            f"plantdiseases_inference_busy_rejections_total {snap['inference_busy_rejections']}",
            "# HELP plantdiseases_analyze_latency_ms_avg Average analyse latency in milliseconds.",
            "# TYPE plantdiseases_analyze_latency_ms_avg gauge",
            f"plantdiseases_analyze_latency_ms_avg {snap['avg_latency_ms']}",
            "# HELP plantdiseases_build_info Build version label.",
            "# TYPE plantdiseases_build_info gauge",
            f'plantdiseases_build_info{{version="{version}"}} 1',
            "# HELP plantdiseases_class_predictions_total Count of predictions per class.",
            "# TYPE plantdiseases_class_predictions_total counter",
        ]
        for class_name, count in sorted(snap["class_counts"].items()):
            lines.append(
                f'plantdiseases_class_predictions_total{{class="{m._prom_label(class_name)}"}} {count}'
            )

        lines.extend(
            [
                "# HELP plantdiseases_plantnet_cache_hits_total PlantNet responses served from the local image cache.",
                "# TYPE plantdiseases_plantnet_cache_hits_total counter",
                f"plantdiseases_plantnet_cache_hits_total {plantnet['cache_hits_total']}",
                "# HELP plantdiseases_plantnet_upstream_attempts_total Real PlantNet network attempts after budget authorization.",
                "# TYPE plantdiseases_plantnet_upstream_attempts_total counter",
                f"plantdiseases_plantnet_upstream_attempts_total {plantnet['upstream_attempts_total']}",
                "# HELP plantdiseases_plantnet_upstream_retries_total Real PlantNet retry attempts after a transient failure.",
                "# TYPE plantdiseases_plantnet_upstream_retries_total counter",
                f"plantdiseases_plantnet_upstream_retries_total {plantnet['upstream_retries_total']}",
                "# HELP plantdiseases_plantnet_budget_denials_total Attempts denied by a configured shared ceiling.",
                "# TYPE plantdiseases_plantnet_budget_denials_total counter",
                f"plantdiseases_plantnet_budget_denials_total {plantnet['budget_denials_total']}",
                "# HELP plantdiseases_plantnet_budget_unavailable_total Attempts denied because the shared authority was unavailable.",
                "# TYPE plantdiseases_plantnet_budget_unavailable_total counter",
                f"plantdiseases_plantnet_budget_unavailable_total {plantnet['budget_unavailable_total']}",
                "# HELP plantdiseases_plantnet_reserved_cost_micros_total Marginal-cost exposure reserved by this process.",
                "# TYPE plantdiseases_plantnet_reserved_cost_micros_total counter",
                f"plantdiseases_plantnet_reserved_cost_micros_total {plantnet['reserved_cost_micros_total']}",
                "# HELP plantdiseases_plantnet_daily_cost_exposure_micros Latest shared UTC-day marginal-cost exposure observed.",
                "# TYPE plantdiseases_plantnet_daily_cost_exposure_micros gauge",
                f"plantdiseases_plantnet_daily_cost_exposure_micros {plantnet['daily_cost_exposure_micros']}",
            ]
        )

        body = "\n".join(lines) + "\n"
        return StarletteResponse(
            content=body,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )


    @router.post("/telemetry/errors")
    async def telemetry_errors(payload: m.TelemetryErrorRequest, request: Request):
        """Record minimized error telemetry only under the current consent text."""
        if not m.TELEMETRY_ENABLED or not payload.telemetry_consent:
            return JSONResponse(content={"ok": True, "recorded": False})
        if (
            (payload.telemetry_consent_version or "").strip().lower()
            != m.TELEMETRY_CONSENT_VERSION.lower()
        ):
            raise HTTPException(status_code=422, detail="telemetry_consent_version_invalid")
        client_ip = m._client_ip(request)
        if not m._consume_daily_write(
            "telemetry", client_ip, m.TELEMETRY_DAILY_CAP_PER_IP
        ):
            raise HTTPException(status_code=429, detail="Daily telemetry quota reached.")
        loop = asyncio.get_running_loop()
        try:
            rid = await loop.run_in_executor(
                None,
                lambda: m.telemetry_record_error(
                    stage=payload.stage,
                    kind=payload.kind,
                    fatal=payload.fatal,
                    http_status_bucket=m.telemetry_http_status_bucket(
                        payload.http_status
                    ),
                    family=payload.family,
                    app_version=payload.app_version,
                    android_api_bucket=m.telemetry_android_api_bucket(
                        payload.android_api
                    ),
                    breadcrumbs=payload.breadcrumbs,
                ),
            )
        except Exception:  # noqa: BLE001 - telemetry never breaks the caller
            m.logger.warning("Minimized telemetry write failed")
            return JSONResponse(content={"ok": True, "recorded": False})
        return JSONResponse(content={"ok": True, "recorded": rid is not None})
