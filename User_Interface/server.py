"""
╔══════════════════════════════════════════════════════════════════════════╗
║          IITGN — SMART TABLE TENNIS TRAINER  /  tt_trainer_backend.py   ║
║          FastAPI  ·  WebSocket  ·  Shared Memory Bridge                  ║
║                                                                          ║
║  Architecture:                                                           ║
║    [CV Process] ──SHM──▶ [Poller @ 60Hz] ──▶ [DrillSession]             ║
║                                                      │                   ║
║                                           [ConnectionManager]            ║
║                                                      │                   ║
║                                            [Phone UI clients]            ║
╚══════════════════════════════════════════════════════════════════════════╝

Install:
    pip install fastapi uvicorn[standard]

Run:
    uvicorn tt_trainer_backend:app --host 0.0.0.0 --port 8000
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
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

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
#  ──────  ────  ──────  ──────────────────────────────────────────────────
#    0      1    uint8   hit_recorded  (latch: CV sets 1, this process clears 0)
#    1      1    uint8   success       (0 = miss, 1 = hit)
#    2      2    uint16  impact_y_mm   (0 – 1525, physical board coords)
#    4      2    uint16  impact_z_mm   (0 – 1000, physical board coords)
#    6      1    uint8   target_zone   (1 – 9, numpad layout, set by THIS process)
#    7      3    bytes   padding
#   10     22    bytes   reserved for future fields
#
SHM_NAME   = "tt_cv_bridge"
SHM_SIZE   = 34
SHM_FMT    = "<B B H H B 3x 22x"   # 10 bytes of payload + 22 reserved = 32
SHM_FIELDS = ("hit_recorded", "success", "impact_y", "impact_z", "target_zone")

POLL_HZ       = 60
POLL_INTERVAL = 1.0 / POLL_HZ


# ─────────────────────────────────────────────────────────────────────────────
# SHARED MEMORY BRIDGE
# ─────────────────────────────────────────────────────────────────────────────
class SharedMemoryBridge:
    """
    Attaches to the shared memory segment written by the CV process.
    Falls back to creating a mock segment if the CV process is not running yet.
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
                    log.info("SHM: segment '%s' unlinked", SHM_NAME)
                except Exception:
                    pass
            self._shm = None

    def read_frame(self) -> dict | None:
        """Returns a shot data dict if a new shot is flagged, else None."""
        if not self._shm:
            return None
        vals = struct.unpack_from(SHM_FMT, bytes(self._shm.buf[:SHM_SIZE]))
        frame = dict(zip(SHM_FIELDS, vals))
        return frame if frame["hit_recorded"] else None

    def acknowledge(self) -> None:
        """Clear the hit_recorded latch so we do not re-process the same shot."""
        if self._shm:
            self._shm.buf[0] = 0

    def write_mock_shot(self, success: bool, y: int, z: int, zone: int) -> None:
        """Dev only: write a fake frame as if the CV process fired."""
        if not self._shm:
            return
        packed = struct.pack(SHM_FMT, 1, int(success), y, z, zone)
        self._shm.buf[:SHM_SIZE] = packed

    def _clear(self) -> None:
        if self._shm:
            self._shm.buf[:SHM_SIZE] = b"\x00" * SHM_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# DIFFICULTY LEVELS
# ─────────────────────────────────────────────────────────────────────────────
# Keys are 3-char prefixes that encode into drill_id (e.g. "BEG_01", "ADV_03").
# interval is a (min_s, max_s) range — mock CV picks uniformly within it.
# scatter_mm is the Gaussian σ around the zone centre for a successful hit.
LEVEL_CONFIG: dict[str, dict] = {
    "BEG": {
        "hit_prob":    0.80,
        "interval":    (3.0, 5.0),
        "scatter_mm":  80,
        "description": "Slow pace, large targets, forgiving accuracy",
    },
    "INT": {
        "hit_prob":    0.65,
        "interval":    (2.0, 3.5),
        "scatter_mm":  60,
        "description": "Moderate pace, tighter zones, more variation",
    },
    "ADV": {
        "hit_prob":    0.50,
        "interval":    (1.5, 2.5),
        "scatter_mm":  40,
        "description": "Fast pace, small targets, maximum pressure",
    },
}

# Zone number (numpad layout) → board centre (y_mm, z_mm)
# Board physical dimensions: 1525 mm wide × 1000 mm tall
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
    active:         bool  = False   # gates shot recording; set by start/stop_drill
    hits:           int   = 0
    misses:         int   = 0
    current_streak: int   = 0
    best_streak:    int   = 0
    start_ts:       float = field(default_factory=time.time)

    # Last shot details (populated after each shot)
    last_impact_y:    int = 0
    last_impact_z:    int = 0
    last_target_zone: int = 5   # default to centre zone

    def level_prefix(self) -> str:
        """Derive the 3-char level key from drill_id, e.g. 'BEG_01' → 'BEG'."""
        return self.drill_id[:3] if self.drill_id else "BEG"

    def get_config(self) -> dict:
        return LEVEL_CONFIG.get(self.level_prefix(), LEVEL_CONFIG["BEG"])

    def next_random_zone(self) -> int:
        """Pick and store a new random zone (1–9). Backend owns this decision."""
        self.last_target_zone = random.randint(1, 9)
        return self.last_target_zone

    @property
    def total_shots(self) -> int:
        return self.hits + self.misses

    @property
    def accuracy_percentage(self) -> float:
        return round(self.hits / max(1, self.total_shots) * 100, 1)

    @property
    def elapsed_seconds(self) -> float:
        return round(time.time() - self.start_ts, 1)

    # (ONLY showing modified + relevant sections — everything else remains SAME)

# ============================
# 🔥 1. MODIFY record_shot()
# ============================
    def record_shot(self, success: bool, impact_y: int, impact_z: int) -> dict:
        self.last_impact_y = impact_y
        self.last_impact_z = impact_z
        
        if success:
            self.hits += 1
            self.current_streak += 1
            self.best_streak = max(self.best_streak, self.current_streak)
        else:
            self.misses += 1
            self.current_streak = 0

        return {
            "event": "shot_result",
            "success": success,
            "impact_coords": {"y": impact_y, "z": impact_z},
            
            # New system fields
            "target_zone": self.last_target_zone,
            "hits_count": self.hits,
            "misses": self.misses,
            "total_shots": self.total_shots,
            "accuracy": self.accuracy_percentage,
            "current_streak": self.current_streak,
            "best_streak": self.best_streak,

            "hit_count": self.hits,
            "streak": self.current_streak,

        # Mock velocity for frontend
            "velocity": random.randint(40, 95)
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
            "current_zone":    self.last_target_zone,
            "last_impact_y":   self.last_impact_y,
            "last_impact_z":   self.last_impact_z,
            "level_config":    self.get_config(),
        }

    def reset(self) -> None:
        self.hits = self.misses = self.current_streak = self.best_streak = 0
        self.start_ts = time.time()
        self.last_target_zone = 5


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class ConnectionManager:
    """Manages all active Phone UI WebSocket connections."""

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
        """Send JSON payload to every connected client; silently drop dead ones."""
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
    log.info("╔══════════════════════════════════════════╗")
    log.info("║   🏓  TT Trainer Brain — STARTING UP     ║")
    log.info("╚══════════════════════════════════════════╝")

    shm_bridge.open()
    poller_task    = asyncio.create_task(shm_poll_loop())
    simulator_task = asyncio.create_task(mock_cv_loop())   # remove in production

    log.info("🚀  All systems go — API ready")
    yield

    log.info("🛑  Shutting down ...")
    poller_task.cancel()
    simulator_task.cancel()
    shm_bridge.close()
    log.info("👋  TT Trainer Brain stopped")


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND: SHM POLL LOOP  (60 Hz)
# ─────────────────────────────────────────────────────────────────────────────
async def shm_poll_loop() -> None:
    """
    Polls shared memory at 60 Hz.  When the CV process sets hit_recorded=1
    we acknowledge FIRST (clear the latch), then update the session and
    broadcast — this order prevents double-reads if processing is slow.

    The CV process runs at 120 fps; polling at 60 Hz halves CPU load with
    no data loss because hit_recorded is a sticky latch.
    """
    log.info("🔁  SHM Poller started @ 60 Hz")

    while True:
        try:
            frame = shm_bridge.read_frame()
            if frame and session.active:
                # Acknowledge before processing — fail-safe against double-reads
                shm_bridge.acknowledge()

                shot = session.record_shot(
                    success  = bool(frame["success"]),
                    impact_y = frame["impact_y"],
                    impact_z = frame["impact_z"],
                )
                await manager.broadcast(shot)

                icon = "✅" if frame["success"] else "❌"
                log.info(
                    "%s  Shot #%d | zone=%d  y=%d z=%d | acc=%.1f%% | streak=%d",
                    icon, session.total_shots, session.last_target_zone,
                    frame["impact_y"], frame["impact_z"],
                    session.accuracy_percentage, session.current_streak,
                )

        except Exception as exc:
            log.error("SHM poll error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND: MOCK CV SIMULATOR  (dev only — remove in production)
# ─────────────────────────────────────────────────────────────────────────────
async def mock_cv_loop() -> None:
    """
    Simulates the CV process by writing to shared memory on a realistic cadence.
    Uses ZONE_CENTERS + Gaussian scatter for physically meaningful coordinates.
    The backend picks the zone here (next_random_zone) — mirroring production,
    where this process writes the target zone into SHM before each shot.
    """
    log.warning("Mock CV loop active — DEVELOPMENT MODE")

    while True:
        cfg = session.get_config()
        await asyncio.sleep(random.uniform(*cfg["interval"]))

        if not session.active:
            continue

        # Skip if the previous hit has not been consumed yet
        if shm_bridge.read_frame() is not None:
            continue

        # Backend owns zone randomisation
        zone    = session.next_random_zone()
        cy, cz  = ZONE_CENTERS[zone]
        success = random.random() < cfg["hit_prob"]
        sigma   = cfg["scatter_mm"]

        if success:
            # Hit: Gaussian scatter around the zone centre
            y = int(cy + random.gauss(0, sigma))
            z = int(cz + random.gauss(0, sigma * 0.6))
        else:
            # Miss: anywhere on the board, uniform
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
    version="2.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────────────────────────────────────────
# ============================
# 3. MODIFY WebSocket connect
# ============================
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)

    # Send sync immediately (IMPORTANT)
    await manager.send(ws, {
        "event": "sync_state",
        "state": {
            "active": session.active,
            "drill_id": session.drill_id,
            "total_shots": session.total_shots,
            "hit_count": session.hits,
            "streak": session.current_streak,
            "best_streak": session.best_streak,
            "accuracy": session.accuracy_percentage
        }
    })

    try:
        while True:
            raw = await ws.receive_text()
            payload = json.loads(raw)
            await handle_message(ws, payload)

    except WebSocketDisconnect:
        manager.disconnect(ws)

# ============================
# 2. MODIFY handle_message()
# ============================
async def handle_message(ws: WebSocket, payload: dict) -> None:
    action = payload.get("action", "")

    # ── GET STATE ──
    if action == "get_state":
        await manager.send(ws, {
            "event": "sync_state",
            "state": {
                "active": session.active,
                "drill_id": session.drill_id,
                "total_shots": session.total_shots,
                "hit_count": session.hits,
                "streak": session.current_streak,
                "best_streak": session.best_streak,
                "accuracy": session.accuracy_percentage
            }
        })
        return

    # ── START DRILL ──
    if action == "start_drill":
        raw_id = payload.get("drill_id", "BEG_01")

        if isinstance(raw_id, int):
            mapping = {
                1: "BEG_01", 2: "BEG_02", 3: "BEG_03",
                4: "INT_01", 5: "INT_02", 6: "INT_03",
                7: "ADV_01", 8: "ADV_02", 9: "ADV_03",
            }
            drill_id = mapping.get(raw_id, "BEG_01")
        else:
            drill_id = str(raw_id)

        prefix = drill_id[:3]

        if prefix not in LEVEL_CONFIG:
            await manager.send(ws, {
                "event": "error",
                "message": f"Invalid drill_id: {drill_id}"
            })
            return

        session.drill_id = drill_id
        session.active = True
        session.reset()

        await manager.broadcast({
            "event": "drill_started",
            "drill_id": drill_id
        })
        return

    # ── STOP DRILL ──
    if action == "stop_drill":
        session.active = False

        await manager.broadcast({
            "event": "drill_stopped",
            "hit_count": session.hits,
            "streak": session.best_streak,
            "accuracy": session.accuracy_percentage,
            "total_shots": session.total_shots
        })
        return

    # ── UNKNOWN ──
    await manager.send(ws, {
        "event": "error",
        "message": f"Unknown action: {action}"
    })


# ─────────────────────────────────────────────────────────────────────────────
# REST ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health() -> dict:
    """Heartbeat — confirms the brain is alive and SHM is attached."""
    return {
        "status":          "ok",
        "shm_open":        shm_bridge._shm is not None,
        "ws_clients":      len(manager.active),
        "drill_active":    session.active,
        "drill_id":        session.drill_id,
        "elapsed_seconds": session.elapsed_seconds,
    }


# ============================
# 4. MODIFY /api/session
# ============================
@app.get("/api/session")
async def get_session():
    return {
        "active": session.active,
        "drill_id": session.drill_id,
        "total_shots": session.total_shots,
        "hit_count": session.hits,
        "streak": session.current_streak,
        "best_streak": session.best_streak,
        "accuracy": session.accuracy_percentage
    }



@app.get("/api/levels")
async def get_levels() -> dict:
    """Return all difficulty level configurations (useful for phone UI onboarding)."""
    return LEVEL_CONFIG


@app.post("/api/inject_shot")
async def inject_shot(body: dict) -> dict:
    """
    Dev endpoint: inject a shot without hardware.
    Backend still randomises the zone — consistent with production behaviour.

    Body: { "success": true, "impact_coords": {"y": 762, "z": 500} }
    The poller will process this within ~17 ms (next 60 Hz tick).
    """
    if not session.active:
        return JSONResponse(
            status_code=409,
            content={"injected": False, "reason": "No active drill — send start_drill first"},
        )

    if shm_bridge.read_frame() is not None:
        return JSONResponse(
            status_code=409,
            content={"injected": False, "reason": "Previous hit not yet consumed — retry in ~17 ms"},
        )

    zone    = session.next_random_zone()
    cy, cz  = ZONE_CENTERS[zone]
    success = bool(body.get("success", True))
    coords  = body.get("impact_coords")
    y = int(coords["y"]) if coords else cy
    z = int(coords["z"]) if coords else cz

    shm_bridge.write_mock_shot(success=success, y=y, z=z, zone=zone)
    log.info("Manual inject — success=%s y=%d z=%d zone=%d", success, y, z, zone)
    return {"injected": True, "zone": zone, "success": success, "y": y, "z": z}


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    uvicorn.run(
        "tt_trainer_backend:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        loop="uvloop" if sys.platform != "win32" else "asyncio",
        log_level="info",
        access_log=False,
    )
