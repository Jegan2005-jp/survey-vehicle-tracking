import asyncio
import json
import logging
import os
from typing import Any, Dict
from pathlib import Path

from pydantic import BaseModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.camera import decode_frame_message
from backend.config import load_settings, setup_logging
from backend.session_manager import SurveySessionManager


# Pydantic models for request validation
class StartSessionRequest(BaseModel):
    cameraType: str = "unknown"
    cameraName: str = "FrontCam"


class StopSessionRequest(BaseModel):
    sessionId: str


def create_app() -> FastAPI:
    base_dir = Path(__file__).resolve().parent
    settings = load_settings()
    setup_logging(settings.log_level)

    logger = logging.getLogger(__name__)
    logger.info("Starting Vehicle Survey server on %s:%s", settings.host, settings.port)

    app = FastAPI(title="Vehicle Survey & Tracking")

    # Serve frontend assets.
    static_dir = base_dir / "frontend" / "static"
    templates_dir = base_dir / "frontend" / "templates"
    app.mount(
        "/static",
        StaticFiles(directory=str(static_dir), html=False),
        name="static",
    )
    templates = Jinja2Templates(directory=str(templates_dir))

    # Frontend and backend are same-origin by default, but keep CORS for safety on mobile.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    session_manager = SurveySessionManager(settings)

    @app.get("/video.mp4")
    async def get_video():
        video_path = base_dir / "video.mp4"
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Video file not found")
        return FileResponse(str(video_path), media_type="video/mp4")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        # TemplateResponse signature: (request, template_name, context)
        return templates.TemplateResponse(request, "index.html", {"request": request})

    @app.post("/api/session/start")
    async def start_session(req: StartSessionRequest) -> Dict[str, Any]:
        """Start a new survey session."""
        camera_type = req.cameraType or "unknown"
        camera_name = req.cameraName or settings.camera_name
        try:
            return session_manager.start_session(camera_type=camera_type, camera_name=camera_name)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=400, detail=f"Configuration/model missing: {e}")
        except Exception as e:
            logger.exception("Failed to start session: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/session/stop")
    async def stop_session(req: StopSessionRequest) -> Dict[str, Any]:
        """Stop the active survey session and return summary."""
        session_id = req.sessionId
        try:
            return session_manager.stop_session(session_id=session_id)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            logger.exception("Failed to stop session: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, sessionId: str):
        await websocket.accept()
        try:
            while True:
                msg = await websocket.receive_text()
                payload = json.loads(msg)

                msg_type = payload.get("type")
                if msg_type == "frame":
                    frame_bgr, frame_number = decode_frame_message(payload)
                    update = await asyncio.to_thread(
                        session_manager.process_frame,
                        session_id=sessionId,
                        frame_bgr=frame_bgr,
                        frame_number=frame_number,
                    )
                    await websocket.send_json(update)
                elif msg_type == "stop":
                    await websocket.close()
                    break
                else:
                    await websocket.send_json({"type": "error", "message": "Unknown message type"})
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected for sessionId=%s", sessionId)
        except Exception as e:
            logger.exception("WebSocket error for sessionId=%s: %s", sessionId, e)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    s = load_settings()
    uvicorn.run("app:app", host=s.host, port=s.port, reload=False)