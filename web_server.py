from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from auth import TokenStore
from config import ConfigManager
from shared_state import SharedState


LOGGER = logging.getLogger(__name__)


def build_app(config_manager: ConfigManager, shared_state: SharedState, token_store: TokenStore) -> FastAPI:
    app = FastAPI(title="GasMonitor")
    web_dir = Path(__file__).parent / "web"
    app.mount("/web", StaticFiles(directory=web_dir), name="web")

    def require_auth(authorization: str | None = Header(default=None)) -> str:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1]
        if not token_store.verify(token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return token

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.post("/login")
    def login(payload: dict[str, str]) -> dict[str, Any]:
        token = token_store.login(payload.get("username", ""), payload.get("password", ""))
        if token is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        snapshot = shared_state.snapshot()
        return {
            "token": token,
            "first_run": snapshot["first_run"],
            "require_password_change": snapshot["require_password_change"],
        }

    @app.get("/api/measurements")
    def measurements(_: str = Depends(require_auth)) -> dict[str, Any]:
        return shared_state.snapshot()

    @app.get("/api/config")
    def get_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return config_manager.to_dict(include_secrets=False)

    @app.post("/api/config")
    def set_config(payload: dict[str, dict[str, Any]], _: str = Depends(require_auth)) -> dict[str, Any]:
        password = payload.get("web", {}).get("password", "")
        if shared_state.snapshot()["first_run"] and not password:
            raise HTTPException(status_code=400, detail="Password change required during first run")

        runtime = config_manager.update(payload)
        if password and runtime.first_run:
            runtime = config_manager.set_first_run(False)
        shared_state.refresh_config(runtime)
        try:
            config_manager.apply_network_profile()
        except Exception as exc:
            LOGGER.warning("network profile was not applied: %s", exc)
        return {"ok": True, "first_run": runtime.first_run}

    @app.post("/api/reboot")
    def reboot(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            subprocess.Popen(["/bin/systemctl", "reboot"])
        except Exception as exc:
            LOGGER.error("reboot request failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True}

    return app


class WebServerThread:
    def __init__(self, config_manager: ConfigManager, shared_state: SharedState, token_store: TokenStore) -> None:
        self.config_manager = config_manager
        self.shared_state = shared_state
        self.token_store = token_store

    def run(self) -> None:
        runtime = self.config_manager.runtime()
        app = build_app(self.config_manager, self.shared_state, self.token_store)
        server = uvicorn.Server(
            uvicorn.Config(app=app, host="0.0.0.0", port=runtime.web_port, log_level="warning")
        )
        server.run()
