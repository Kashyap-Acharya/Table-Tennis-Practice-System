"""
╔══════════════════════════════════════════════════════════════════════════╗
║          IITGN — SMART TABLE TENNIS TRAINER  /  api_server.py            ║
║          FastAPI  ·  WebSocket  ·  Multiprocessing Queue                 ║
║                                                                          ║
║  Architecture:                                                           ║
║    [CV Process] ──SHM──▶ [Poller @ 60Hz] ──▶ [DrillSession]              ║
║                                                      │                   ║
║    [Phone UI] ◀──WS──▶ [FastAPI] ──Queue──▶ [Launcher Engine]            ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from multiprocessing.shared_memory import SharedMemory
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

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
SHM_SIZE   = 34
SHM_FMT    = "<B B H H B 3x 22x"   
SHM_FIELDS = ("hit_recorded", "success", "impact_y", "impact_z", "target_zone")

POLL_HZ       = 60
POLL_INTERVAL = 1.0 / POLL_HZ

# ─────────────────────────────────────────────────────────────────────────────
# SHARED MEMORY BRIDGE (Strictly a Reader)
# ─────────────────────────────────────────────────────────────────────────────
class SharedMemoryBridge:
    def __init__(self) -> None:
        self._shm: SharedMemory | None = None

    def try_connect(self) -> bool:
        """Attempts to attach to the CV process memory block."""
        if self._shm:
            return True
        try:
            self._shm = SharedMemory(name=SHM_NAME, create=False)
            log.info("SHM: Successfully attached to CV segment '%s'", SHM_NAME)
            return True
        except FileNotFoundError:
            return False # Vision engine hasn't booted yet

    def close(self) -> None:
        if self._shm:
            self._shm.close()
            self._shm = None

    def read_frame(self) -> dict | None:
        if not self._shm:
            return None
        vals = struct.unpack_from(SHM_FMT, bytes(self._shm.buf[:SHM_SIZE]))
        frame = dict(zip(SHM_FIELDS, vals))
        return frame if frame["hit_recorded"] else None

    def acknowledge(self) -> None:
        """Clears the hit latch so we don't double-count the shot."""
        if self._shm:
            self._shm.buf[0] = 0


# ─────────────────────────────────────────────────────────────────────────────
# DIFFICULTY LEVELS
# ─────────────────────────────────────────────────────────────────────────────
LEVEL_CONFIG: dict[str, dict] = {
    "BEG": {"description": "Slow pace, large targets, forgiving accuracy"},
    "INT": {"description": "Moderate pace, tighter zones, more variation"},
    "ADV": {"description": "Fast pace, small targets, maximum pressure"},
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

    @property
    def total_shots(self) -> int:
        return self.hits + self.misses

    @property
    def accuracy_percentage(self) -> float:
        return round(self.hits / max(1, self.total_shots) * 100, 1)

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

        return {
            "event":           "shot_result",
            "success":         success,
            "impact_coords":   {"y": impact_y, "z": impact_z},
            "target_zone":     self.last_target_zone,
            "hits":            self.hits,
            "misses":          self.misses,
            "total_shots":     self.total_shots,
            "accuracy":        self.accuracy_percentage,
            "current_streak":  self.current_streak,
            "best_streak":     self.best_streak,
        }

    def to_dict(self) -> dict:
        return {
            "drill_id":        self.drill_id,
            "active":          self.active,
            "hits":            self.hits,
            "misses":          self.misses,
            "total_shots":     self.total_shots,
            "accuracy":        self.accuracy_percentage,
            "current_streak":  self.current_streak,
            "best_streak":     self.best_streak,
            "elapsed_seconds": self.elapsed_seconds,
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

    poller_task = asyncio.create_task(shm_poll_loop())
    yield
    log.info("🛑  Shutting down ...")
    poller_task.cancel()
    shm_bridge.close()

# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND: SHM POLL LOOP  (60 Hz)
# ─────────────────────────────────────────────────────────────────────────────
async def shm_poll_loop() -> None:
    log.info("🔁  SHM Poller waiting for Vision Engine...")
    while True:
        try:
            # Safely wait for the Vision process to create the memory block
            if not shm_bridge.try_connect():
                await asyncio.sleep(1)
                continue

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
                    "%s Shot #%d | zone=%d | acc=%.1f%% | streak=%d",
                    icon, session.total_shots, frame["target_zone"],
                    session.accuracy_percentage, session.current_streak
                )
        except Exception as exc:
            log.error("SHM poll error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)

# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TT Trainer API",
    description="Smart Table Tennis Trainer — Web Server Process",
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    await manager.send(ws, {
        "event":   "welcome",
        "session": session.to_dict(),
    })
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
                await handle_message(ws, payload)
            except json.JSONDecodeError:
                continue
    except WebSocketDisconnect:
        manager.disconnect(ws)

async def handle_message(ws: WebSocket, payload: dict) -> None:
    action = payload.get("action", "")

    if action == "ping":
        await manager.send(ws, {"event": "pong", "ts": time.time()})
        return

    # ── START DRILL ──────────────────────────────────────────────────────────
    if action == "start_drill":
        drill_id = str(payload.get("drill_id", "BEG_01"))
        prefix   = drill_id[:3]

        if prefix not in LEVEL_CONFIG:
            await manager.send(ws, {"event": "error", "message": "Unknown level"})
            return

        session.drill_id = drill_id
        session.active   = True
        session.reset()

        # ── IPC: SEND TO LAUNCHER PROCESS ──
        if hasattr(app.state, 'command_queue'):
            app.state.command_queue.put({"action": "start", "drill_id": drill_id})

        await manager.broadcast({"event": "drill_started", "drill_id": drill_id})
        return

    # ── STOP DRILL ───────────────────────────────────────────────────────────
    if action == "stop_drill":
        session.active = False
        
        # ── IPC: SEND TO LAUNCHER PROCESS ──
        if hasattr(app.state, 'command_queue'):
            app.state.command_queue.put({"action": "stop"})
        
        await manager.broadcast({
            "event":       "drill_stopped",
            "hits":        session.hits,
            "accuracy":    session.accuracy_percentage,
        })
        return

# ─────────────────────────────────────────────────────────────────────────────
# REST ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
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
    return session.to_dict()

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT (Called by the root main.py orchestrator)
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
