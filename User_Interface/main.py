"""
╔══════════════════════════════════════════════════════════════════════════╗
║          IITGN — SMART TABLE TENNIS TRAINER  /  main.py                 ║
║          FastAPI  ·  WebSocket  ·  Shared Memory Bridge                  ║
║                                                                          ║
║  Key design decisions vs v1:                                             ║
║    • Zones are randomized per shot by the backend (not chosen by user)   ║
║    • No launcher speed control — drill pace is fixed per level           ║
║    • No per-shot event log push — canvas + streak metrics only           ║
║    • start_drill payload is minimal: { action, drill_id }                ║
╚══════════════════════════════════════════════════════════════════════════╝

Install:
    pip install fastapi uvicorn[standard]

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import struct
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from multiprocessing.shared_memory import SharedMemory

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

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
# SHARED MEMORY SCHEMA  (32-byte block written by CV process)
# ─────────────────────────────────────────────────────────────────────────────
#  Offset  Size  Type    Field
#  ──────  ────  ──────  ─────────────────────────────────────
#    0      1    uint8   hit_recorded   (latch: 1 = new shot ready)
#    1      1    uint8   success        (0 = miss, 1 = hit)
#    2      2    uint16  impact_y_mm    (0 – 1525)
#    4      2    uint16  impact_z_mm    (0 – 1000)
#    6      1    uint8   target_zone    (1 – 9, set by THIS process before firing)
#    7      9    bytes   padding
#   16     16    bytes   reserved
#
SHM_NAME   = "tt_cv_bridge"
SHM_SIZE   = 32
SHM_FMT    = "<BBHHBx 9x 16x"   # little-endian; 1+1+2+2+1+1+9+16 = 33 → pad to 32
# Correct struct: hit_recorded(1) success(1) y(2) z(2) zone(1) = 7 bytes + 25 pad
SHM_FMT    = "<B B H H B 3x 8x 16x"
SHM_FIELDS = ("hit_recorded", "success", "impact_y", "impact_z", "target_zone")


# ─────────────────────────────────────────────────────────────────────────────
# SHARED MEMORY BRIDGE
# ─────────────────────────────────────────────────────────────────────────────
class SharedMemoryBridge:
    """
    Attaches to the shared memory segment written by the CV process.
    Falls back to creating a mock segment if the CV process isn't running.
    """

    def __init__(self) -> None:
        self._shm: SharedMemory | None = None
        self._owner = False

    def open(self) -> None:
        try:
            self._shm = SharedMemory(name=SHM_NAME, create=False, size=SHM_SIZE)
            log.info("SHM: attached to existing segment '%s'", SHM_NAME)
        except FileNotFoundError:
            self._shm = SharedMemory(name=SHM_NAME, create=True, size=SHM_SIZE)
            self._owner = True
            log.warning("SHM: segment not found — created mock segment (dev mode)")
            self._clear()

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
        """Returns shot data dict if a new shot is flagged, else None."""
        if not self._shm:
            return None
        raw = bytes(self._shm.buf[:SHM_SIZE])
        vals = struct.unpack_from(SHM_FMT, raw)
        frame = dict(zip(SHM_FIELDS, vals))
        return frame if frame["hit_recorded"] else None

    def acknowledge(self) -> None:
        """Clear the hit_recorded latch so we don't re-process the same shot."""
        if self._shm:
            self._shm.buf[0] = 0

    def write_mock_shot(
        self,
        success: bool,
        y: int,
        z: int,
        zone: int,
    ) -> None:
        """Dev only: write a fake frame as if the CV process fired."""
        if not self._shm:
            return
        data = struct.pack(SHM_FMT, 1, int(success), y, z, zone)
        self._shm.buf[:SHM_SIZE] = data + b"\x00" * (SHM_SIZE - len(data))

    def _clear(self) -> None:
        if self._shm:
            self._shm.buf[:SHM_SIZE] = b"\x00" * SHM_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# DRILL LEVEL CONFIG  (no speed — only affects hit probability & shot cadence)
# ─────────────────────────────────────────────────────────────────────────────
LEVEL_CONFIG: dict[str, dict] = {
    # drill_id prefix → {hit_prob, interval_range_s, scatter_mm}
    "BEG": {"hit_prob": 0.80, "interval": (3.0, 5.0), "scatter": 80},
    "INT": {"hit_prob": 0.65, "interval": (2.0, 3.5), "scatter": 60},
    "ADV": {"hit_prob": 0.50, "interval": (1.5, 2.5), "scatter": 40},
}

# Zone number (numpad layout) → board center (y_mm, z_mm)
# Board: 1525 mm wide × 1000 mm tall
ZONE_CENTERS: dict[int, tuple[int, int]] = {
    7: (254, 833), 8: (762, 833), 9: (1270, 833),
    4: (254, 500), 5: (762, 500), 6: (1270, 500),
    1: (254, 167), 2: (762, 167), 3: (1270, 167),
}


# ─────────────────────────────────────────────────────────────────────────────
# DRILL SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class DrillSession:
    drill_id:    str  = ""
    active:      bool = False
    hit_count:   int  = 0
    miss_count:  int  = 0
    streak:      int  = 0
    best_streak: int  = 0

    # Last zone randomly assigned this shot
    current_zone: int = 5

    def level_prefix(self) -> str:
        return self.drill_id[:3] if self.drill_id else "BEG"

    def get_config(self) -> dict:
        return LEVEL_CONFIG.get(self.level_prefix(), LEVEL_CONFIG["BEG"])

    def next_random_zone(self) -> int:
        """Pick a new random zone from 1–9 (uniform)."""
        self.current_zone = random.randint(1, 9)
        return self.current_zone

    def record_shot(self, success: bool, y: int, z: int) -> dict:
        if success:
            self.hit_count += 1
            self.streak    += 1
            self.best_streak = max(self.best_streak, self.streak)
        else:
            self.miss_count += 1
            self.streak      = 0

        return {
            "event":         "shot_result",
            "success":       success,
            "impact_coords": {"y": y, "z": z},
            "target_zone":   self.current_zone,
            "streak":        self.streak,
            "total_shots":   self.hit_count + self.miss_count,
            "accuracy":      round(
                self.hit_count / max(1, self.hit_count + self.miss_count) * 100, 1
            ),
        }

    def reset(self) -> None:
        self.hit_count = self.miss_count = self.streak = self.best_streak = 0
        self.current_zone = 5


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        log.info("WS client connected (total: %d)", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)
        log.info("WS client disconnected (remaining: %d)", len(self.active))

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


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETONS
# ─────────────────────────────────────────────────────────────────────────────
shm_bridge = SharedMemoryBridge()
manager    = ConnectionManager()
session    = DrillSession()


# ─────────────────────────────────────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    shm_bridge.open()
    t1 = asyncio.create_task(shm_poll_loop())
    t2 = asyncio.create_task(mock_cv_loop())   # remove in production
    log.info("TT Trainer server ready  (UART 921,600 baud · CV 120 fps)")
    yield
    t1.cancel(); t2.cancel()
    shm_bridge.close()


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND: SHM POLL
# ─────────────────────────────────────────────────────────────────────────────
async def shm_poll_loop() -> None:
    """
    Polls shared memory at 60 Hz.  Whenever the CV process sets hit_recorded=1
    we read, broadcast the shot_result, then acknowledge (clear the latch).

    The CV process runs at 120 fps; polling at 60 Hz halves CPU load with
    zero data loss because hit_recorded is a sticky latch.
    """
    SLEEP = 1.0 / 60

    while True:
        try:
            frame = shm_bridge.read_frame()
            if frame and session.active:
                shot = session.record_shot(
                    success = bool(frame["success"]),
                    y       = frame["impact_y"],
                    z       = frame["impact_z"],
                )
                await manager.broadcast(shot)
                shm_bridge.acknowledge()
        except Exception as e:
            log.error("SHM poll: %s", e)

        await asyncio.sleep(SLEEP)


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND: MOCK CV SIMULATOR  (dev only — remove in production)
# ─────────────────────────────────────────────────────────────────────────────
async def mock_cv_loop() -> None:
    """
    Simulates the CV process by writing to shared memory on a realistic cadence.
    Each shot gets a NEWLY RANDOMIZED zone chosen by this process.
    In production, the real CV process writes frames; this task is removed.
    """
    log.warning("Mock CV loop active — DEVELOPMENT MODE")

    while True:
        cfg = session.get_config()
        await asyncio.sleep(random.uniform(*cfg["interval"]))

        if not session.active:
            continue

        # ── Randomize zone here (backend owns this) ──────────────────────────
        zone       = session.next_random_zone()
        cy, cz     = ZONE_CENTERS[zone]
        success    = random.random() < cfg["hit_prob"]
        scatter    = cfg["scatter"]

        if success:
            # Hit: land near zone centre with Gaussian scatter
            y = int(cy + random.gauss(0, scatter))
            z = int(cz + random.gauss(0, scatter * 0.6))
        else:
            # Miss: land anywhere on the board, biased away from the zone
            y = random.randint(0, 1525)
            z = random.randint(0, 1000)

        y = max(0, min(1525, y))
        z = max(0, min(1000, z))

        shm_bridge.write_mock_shot(success=success, y=y, z=z, zone=zone)


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TT Trainer API",
    description="Smart Table Tennis Trainer — IITGN CV System",
    version="2.1.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    await manager.send(ws, {
        "event":   "status",
        "message": "CONNECTED — TT TRAINER v2.1 / IITGN",
    })

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send(ws, {"event": "error", "message": "Invalid JSON"})
                continue
            await handle_message(ws, payload)
    except WebSocketDisconnect:
        manager.disconnect(ws)


async def handle_message(ws: WebSocket, payload: dict) -> None:
    action = payload.get("action", "")

    # ── PING ─────────────────────────────────────────────────────────────────
    if action == "ping":
        await manager.send(ws, {"event": "pong", "ts": time.time()})
        return

    # ── START DRILL ──────────────────────────────────────────────────────────
    #
    # Simplified schema — no target_zone, no launcher_speed:
    # {
    #   "action":   "start_drill",
    #   "drill_id": "BEG_01"
    # }
    if action == "start_drill":
        drill_id = str(payload.get("drill_id", "BEG_01"))

        session.drill_id = drill_id
        session.active   = True
        session.reset()

        log.info("Drill started: %s (level: %s)", drill_id, session.level_prefix())

        # ── UART NOTE ─────────────────────────────────────────────────────────
        # In production, send the drill-start command to the Pico here.
        # Example with pyserial at 921,600 baud:
        #
        #   import serial
        #   pico = serial.Serial('/dev/ttyACM0', 921600, timeout=0.01)
        #   cmd  = f"DRILL:{drill_id}\n".encode()
        #   pico.write(cmd); pico.flush()
        #
        # At 921,600 baud a 16-byte packet takes ~0.14 ms — the launcher
        # mechanism is armed well before the first ball is fed.
        # ─────────────────────────────────────────────────────────────────────

        await manager.broadcast({
            "event":    "drill_started",
            "drill_id": drill_id,
            "message":  f"Drill {drill_id} active — zones randomized",
        })
        return

    # ── STOP DRILL ───────────────────────────────────────────────────────────
    if action == "stop_drill":
        session.active = False
        total = session.hit_count + session.miss_count
        log.info(
            "Drill stopped: hits=%d misses=%d best_streak=%d acc=%.1f%%",
            session.hit_count, session.miss_count, session.best_streak,
            session.hit_count / max(1, total) * 100,
        )
        await manager.broadcast({
            "event":       "drill_stopped",
            "hits":        session.hit_count,
            "misses":      session.miss_count,
            "best_streak": session.best_streak,
            "accuracy":    round(session.hit_count / max(1, total) * 100, 1),
        })
        return

    await manager.send(ws, {"event": "error", "message": f"Unknown action: {action}"})


# ─────────────────────────────────────────────────────────────────────────────
# REST HELPERS
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health() -> dict:
    return {
        "status":       "ok",
        "shm_open":     shm_bridge._shm is not None,
        "ws_clients":   len(manager.active),
        "drill_active": session.active,
        "drill_id":     session.drill_id,
    }


@app.get("/api/session")
async def get_session() -> dict:
    total = session.hit_count + session.miss_count
    return {
        "drill_id":     session.drill_id,
        "active":       session.active,
        "hit_count":    session.hit_count,
        "miss_count":   session.miss_count,
        "streak":       session.streak,
        "best_streak":  session.best_streak,
        "accuracy":     round(session.hit_count / max(1, total) * 100, 1),
        "current_zone": session.current_zone,
    }


@app.post("/api/inject_shot")
async def inject_shot(body: dict) -> dict:
    """
    Dev endpoint: inject a shot directly without hardware.
    The backend still randomizes the zone.

    Body:
      { "success": true, "impact_coords": {"y": 762, "z": 500} }
    """
    if not session.active:
        return {"injected": False, "reason": "No active drill"}

    # Randomize zone server-side
    zone   = session.next_random_zone()
    cy, cz = ZONE_CENTERS[zone]

    success = bool(body.get("success", True))
    coords  = body.get("impact_coords")
    if coords:
        y, z = int(coords.get("y", cy)), int(coords.get("z", cz))
    else:
        y, z = cy, cz

    shot = session.record_shot(success, y, z)
    await manager.broadcast(shot)
    return {"injected": True, "shot": shot}


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        loop="uvloop" if sys.platform != "win32" else "asyncio",
        log_level="info",
        access_log=False,
    )
