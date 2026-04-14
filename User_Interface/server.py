"""
╔══════════════════════════════════════════════════════════════════════════╗
║          IITGN — SMART TABLE TENNIS TRAINER  /  server.py                ║
║          FastAPI  ·  WebSocket  ·  Multiprocessing Queue                 ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import random
import time
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from multiprocessing.shared_memory import SharedMemory
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tt-trainer")

# ─────────────────────────────────────────────────────────────────────────────
# SHARED MEMORY SCHEMA  (Read-Only from Web Server perspective)
# ─────────────────────────────────────────────────────────────────────────────
SHM_NAME   = "tt_cv_bridge"
SHM_FMT    = "<B B H H B 3x 22x"   
SHM_SIZE   = struct.calcsize(SHM_FMT) # Dynamically calculate to prevent ValueError crashes
SHM_FIELDS = ("hit_recorded", "success", "impact_y", "impact_z", "target_zone")

POLL_HZ       = 60
POLL_INTERVAL = 1.0 / POLL_HZ

# ─────────────────────────────────────────────────────────────────────────────
# SHARED MEMORY BRIDGE (Strictly a Reader + Mocker for Dev)
# ─────────────────────────────────────────────────────────────────────────────
class SharedMemoryBridge:
    def __init__(self) -> None:
        self._shm: SharedMemory | None = None
        self._owner = False

    def try_connect(self) -> bool:
        if self._shm:
            return True
        try:
            self._shm = SharedMemory(name=SHM_NAME, create=False)
            log.info("SHM: Successfully attached to CV segment '%s'", SHM_NAME)
            return True
        except FileNotFoundError:
            # Fallback for dev mode
            self._shm = SharedMemory(name=SHM_NAME, create=True, size=SHM_SIZE)
            self._owner = True
            log.warning("SHM: segment not found — created mock segment (dev mode)")
            self._clear()
            return True

    def close(self) -> None:
        if self._shm:
            self._shm.close()
            if self._owner:
                try:
                    self._shm.unlink()
                except Exception:
                    pass
            self._shm = None

    def read_frame(self) -> dict | None:
        if not self._shm:
            return None
        vals = struct.unpack_from(SHM_FMT, bytes(self._shm.buf[:SHM_SIZE]))
        frame = dict(zip(SHM_FIELDS, vals))
        return frame if frame["hit_recorded"] else None

    def acknowledge(self) -> None:
        if self._shm:
            self._shm.buf[0] = 0

    def write_mock_shot(self, success: bool, y: int, z: int, zone: int) -> None:
        if not self._shm:
            return
        packed = struct.pack(SHM_FMT, 1, int(success), y, z, zone)
        self._shm.buf[:SHM_SIZE] = packed

    def _clear(self) -> None:
        if self._shm:
            self._shm.buf[:SHM_SIZE] = b"\x00" * SHM_SIZE

# ─────────────────────────────────────────────────────────────────────────────
# DIFFICULTY LEVELS & ZONES
# ─────────────────────────────────────────────────────────────────────────────
LEVEL_CONFIG: dict[str, dict] = {
    "BEG": {"interval": (3.0, 5.0), "hit_prob": 0.80, "scatter_mm": 80},
    "INT": {"interval": (2.0, 3.5), "hit_prob": 0.65, "scatter_mm": 60},
    "ADV": {"interval": (1.5, 2.5), "hit_prob": 0.50, "scatter_mm": 40},
}

ZONE_CENTERS: dict[int, tuple[int, int]] = {
    7: (254, 833), 8: (762, 833), 9: (1270, 833),
    4: (254, 500), 5: (762, 500), 6: (1270, 500),
    1: (254, 167), 2: (762, 167), 3: (1270, 167),
}

# ─────────────────────────────────────────────────────────────────────────────
# DRILL SESSION — stateful shot tracker
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class DrillSession:
    drill_id:       str   = ""
    active:         bool  = False   
    hits:           int   = 0
    misses:         int   = 0
    current_streak: int   = 0
    best_streak:    int   = 0
    start_ts:       float = field(default_factory=time.time)

    last_impact_y:    int = 0
    last_impact_z:    int = 0
    last_target_zone: int = 5   

    def level_prefix(self) -> str:
        return self.drill_id[:3] if self.drill_id else "BEG"
        
    def get_config(self) -> dict:
        return LEVEL_CONFIG.get(self.level_prefix(), LEVEL_CONFIG["BEG"])

    def next_random_zone(self) -> int:
        self.last_target_zone = random.randint(1, 9)
        return self.last_target_zone

    @property
    def total_shots(self) -> int:
        return self.hits + self.misses

    @property
    def accuracy_percentage(self) -> int:
        return int(round(self.hits / max(1, self.total_shots) * 100, 0))

    @property
    def elapsed_seconds(self) -> float:
        return round(time.time() - self.start_ts, 1)

    def record_shot(self, success: bool, impact_y: int, impact_z: int, target_zone: int) -> dict:
        self.last_impact_y = impact_y
        self.last_impact_z = impact_z
        self.last_target_zone = target_zone

        if success:
            self.hits           += 1
            self.current_streak += 1
            self.best_streak     = max(self.best_streak, self.current_streak)
        else:
            self.misses         += 1
            self.current_streak  = 0

        # Ensures UI gets what it needs through the WS packet
        return {
            "event":           "shot_result",
            "success":         success,
            "impact_coords":   {"y": impact_y, "z": impact_z},
            "target_zone":     self.last_target_zone,
            "velocity":        random.randint(40, 95), 
            "hit_count":       self.hits,
            "miss_count":      self.misses,
            "total_shots":     self.total_shots,
            "accuracy":        self.accuracy_percentage,
            "streak":          self.current_streak,
            "best_streak":     self.best_streak,
        }

    def reset(self) -> None:
        self.hits = self.misses = self.current_streak = self.best_streak = 0
        self.start_ts = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def send(self, ws: WebSocket, payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            self.disconnect(ws)


shm_bridge = SharedMemoryBridge()
manager    = ConnectionManager()
session    = DrillSession()

# ─────────────────────────────────────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("╔══════════════════════════════════════════╗")
    log.info("║   🏓  TT Trainer Web Server — READY      ║")
    log.info("╚══════════════════════════════════════════╝")

    shm_bridge.try_connect()
    poller_task = asyncio.create_task(shm_poll_loop())
    simulator_task = asyncio.create_task(mock_cv_loop())
    
    yield
    
    log.info("🛑  Shutting down ...")
    poller_task.cancel()
    simulator_task.cancel()
    shm_bridge.close()

# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND: SHM POLL LOOP  (60 Hz)
# ─────────────────────────────────────────────────────────────────────────────
async def shm_poll_loop() -> None:
    while True:
        try:
            frame = shm_bridge.read_frame()
            if frame and session.active:
                shm_bridge.acknowledge()
                shot = session.record_shot(
                    success     = bool(frame["success"]),
                    impact_y    = frame["impact_y"],
                    impact_z    = frame["impact_z"],
                    target_zone = frame["target_zone"]
                )
                await manager.broadcast(shot)

                icon = "✅" if frame["success"] else "❌"
                log.info(
                    "%s Shot #%d | zone=%d | acc=%d%% | streak=%d",
                    icon, session.total_shots, frame["target_zone"],
                    session.accuracy_percentage, session.current_streak
                )
        except Exception as exc:
            log.error("SHM poll error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)

# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND: MOCK CV SIMULATOR
# ─────────────────────────────────────────────────────────────────────────────
async def mock_cv_loop() -> None:
    log.warning("Mock CV loop active — DEVELOPMENT MODE")
    while True:
        cfg = session.get_config()
        await asyncio.sleep(random.uniform(*cfg["interval"]))

        if not session.active or shm_bridge.read_frame() is not None:
            continue

        zone    = session.next_random_zone()
        cy, cz  = ZONE_CENTERS[zone]
        success = random.random() < cfg["hit_prob"]
        sigma   = cfg["scatter_mm"]

        if success:
            y = int(cy + random.gauss(0, sigma))
            z = int(cz + random.gauss(0, sigma * 0.6))
        else:
            y = random.randint(0, 1525)
            z = random.randint(0, 1000)

        # Cap values safely to table dimensions
        y_safe = max(0, min(1525, y))
        z_safe = max(0, min(1000, z))

        shm_bridge.write_mock_shot(success, y_safe, z_safe, zone)

# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TT Trainer API",
    description="Smart Table Tennis Trainer — Web Server Process",
    lifespan=lifespan,
)

# Add CORS so phones don't block the API calls on local networks
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    
    # Send initial state so frontend syncs immediately
    await manager.send(ws, {
        "event": "sync_state",
        "state": {
            "is_running": session.active,
            "drill_id": session.drill_id,
        }
    })
    
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
                await handle_message(ws, payload)
            except json.JSONDecodeError:
                log.warning("Received invalid JSON over WS")
                continue
    except WebSocketDisconnect:
        manager.disconnect(ws)

async def handle_message(ws: WebSocket, payload: dict) -> None:
    action = payload.get("action", "")

    # ── START DRILL ──────────────────────────────────────────────────────────
    if action == "start_drill":
        raw_id = payload.get("drill_id", "BEG_01")
        
        # Translate integer IDs from HTML to internal string codes
        try:
            drill_num = int(raw_id)
            mapping = {
                1: "BEG_01", 2: "BEG_02", 3: "BEG_03",
                4: "INT_01", 5: "INT_02", 6: "INT_03",
                7: "ADV_01", 8: "ADV_02", 9: "ADV_03",
            }
            drill_id = mapping.get(drill_num, "BEG_01")
        except ValueError:
            drill_id = str(raw_id)

        prefix = drill_id[:3]
        if prefix not in LEVEL_CONFIG:
            await manager.send(ws, {"event": "error", "message": "Unknown level"})
            return

        session.drill_id = drill_id
        session.active   = True
        session.reset()

        # Safely pass command to engine if queue exists
        if hasattr(app.state, 'command_queue'):
            app.state.command_queue.put({"action": "start", "drill_id": drill_id})

        await manager.broadcast({"event": "drill_started", "drill_id": drill_id})
        return

    # ── STOP DRILL ───────────────────────────────────────────────────────────
    if action == "stop_drill":
        session.active = False
        
        if hasattr(app.state, 'command_queue'):
            app.state.command_queue.put({"action": "stop"})
        
        await manager.broadcast({
            "event":       "drill_stopped",
            "hit_count":   session.hits,
            "accuracy":    session.accuracy_percentage,
        })
        return

# ─────────────────────────────────────────────────────────────────────────────
# REST ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def get_dashboard() -> HTMLResponse:
    # Uses pathlib to guarantee it finds index.html even if run from a different folder
    html_path = Path(__file__).parent / "index.html"
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Error: index.html not found!</h1><p>Ensure it is in the same folder as server.py</p>", status_code=404)

@app.get("/api/health")
async def health() -> dict:
    return {
        "status":       "ok",
        "shm_open":     shm_bridge._shm is not None,
        "ws_clients":   len(manager.active),
        "drill_active": session.active,
    }

@app.get("/api/session")
async def get_session() -> dict:
    # Exact property names expected by the frontend's syncStatsWithBackend()
    return {
        "active": session.active,
        "drill_id": session.drill_id,
        "total_shots": session.total_shots,
        "hit_count": session.hits,
        "miss_count": session.misses,
        "streak": session.current_streak,
        "best_streak": session.best_streak,
        "accuracy": session.accuracy_percentage,
        "elapsed_seconds": session.elapsed_seconds,
    }

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def run_web_server(command_queue):
    app.state.command_queue = command_queue
    import sys
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0",
        port=8000,
        workers=1,
        loop="uvloop" if sys.platform != "win32" else "asyncio",
        log_level="info",
        access_log=False,
    )

if __name__ == "__main__":
    import uvicorn
    # Provides fallback for running `python server.py` directly instead of uvicorn CLI
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
code
