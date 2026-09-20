"""Interface web temps réel : état, journal, et bouton de rafraîchissement.

Le radar tourne dans un thread ; FastAPI sert ui/index.html et trois routes :
GET /api/state, POST /api/refresh (cooldown 60 s), GET /api/events (SSE).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from pathlib import Path

import secrets

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from radarlib.core import Radar

UI_FILE = Path(__file__).resolve().parent.parent / "ui" / "index.html"
REFRESH_COOLDOWN_SECONDS = 60


def _tail(path: Path, n: int = 50) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        return [line.decode("utf-8", "replace").rstrip() for line in deque(fh, maxlen=n)]


def build_app(radar: Radar) -> FastAPI:
    token = radar.config.ui_token
    basic = HTTPBasic(auto_error=False)

    def guard(credentials: HTTPBasicCredentials | None = Depends(basic)) -> None:
        """Sans UI_TOKEN : accès libre (usage local). Avec : mot de passe exigé, utilisateur libre."""
        if token is None:
            return
        if credentials is None or not secrets.compare_digest(credentials.password.encode(), token.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mot de passe requis", headers={"WWW-Authenticate": "Basic"})

    app = FastAPI(title="Radar Pokémon 30 ans", dependencies=[Depends(guard)])
    last_refresh = {"at": 0.0}

    def snapshot() -> dict:
        st = radar.state
        products = []
        for p in st.products.values():
            products.append({
                "product_id": p.product_id, "title": p.title, "url": p.url, "price": p.price,
                "status": p.status.value, "web_available": p.web_available, "stores_known": p.stores_known,
                "stores": [{"id": i, "name": p.store_names.get(i, str(i)),
                            "priority": any(k in p.store_names.get(i, "").lower() for k in radar.config.priority_stores)}
                           for i in p.store_ids],
                "last_checked": p.last_checked.isoformat() if p.last_checked else None,
                "last_change": p.last_change.isoformat() if p.last_change else None,
                "last_error": p.last_error, "consecutive_errors": p.consecutive_errors,
            })
        return {
            "version": radar.version,
            "radar": {
                "started_at": st.started_at, "last_cycle_at": st.last_cycle_at, "next_cycle_at": st.next_cycle_at,
                "last_cycle_duration": st.last_cycle_duration, "cycles": st.cycles, "blocked": st.blocked,
                "consecutive_failed_cycles": st.consecutive_failed_cycles, "last_message": st.last_message,
                "interval_seconds": radar.config.interval_seconds, "stores_watched": len(radar.config.store_ids),
                "ntfy_configured": radar.notifier.configured,
                "refresh_available_in": max(0, int(REFRESH_COOLDOWN_SECONDS - (time.time() - last_refresh["at"]))),
            },
            "products": products,
            "log": _tail(radar.config.log_file),
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return UI_FILE.read_text(encoding="utf-8")

    @app.get("/tech", response_class=HTMLResponse)
    def tech() -> str:
        """Même page, ouverte directement en vue technique."""
        return UI_FILE.read_text(encoding="utf-8")

    @app.get("/api/state")
    def state() -> dict:
        return snapshot()

    @app.post("/api/refresh")
    def refresh() -> JSONResponse:
        remaining = REFRESH_COOLDOWN_SECONDS - (time.time() - last_refresh["at"])
        if remaining > 0:
            return JSONResponse({"ok": False, "retry_in": int(remaining)}, status_code=429)
        last_refresh["at"] = time.time()
        radar.request_refresh()
        return JSONResponse({"ok": True})

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        async def stream():
            seen = -1
            while not radar.stop_event.is_set():  # sinon le flux SSE empêche l'arrêt propre
                if radar.version != seen:
                    seen = radar.version
                    yield f"data: {json.dumps(snapshot(), ensure_ascii=False)}\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def serve(radar: Radar) -> None:
    thread = threading.Thread(target=radar.run_forever, name="radar-loop", daemon=True)
    thread.start()
    app = build_app(radar)
    print(f"Interface : http://{radar.config.ui_host}:{radar.config.ui_port}")
    def on_stop() -> None:
        radar.stop_event.set()
        radar.request_refresh()

    try:
        # Sans délai de grâce, uvicorn attendrait indéfiniment la fin des flux SSE
        # ouverts par l'interface, et Ctrl+C ne stopperait jamais le radar.
        uvicorn.run(app, host=radar.config.ui_host, port=radar.config.ui_port, log_level="warning",
                    timeout_graceful_shutdown=3)
    finally:
        on_stop()
