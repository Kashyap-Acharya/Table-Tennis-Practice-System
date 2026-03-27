"""
╔══════════════════════════════════════════════════════════════╗
║        SMART TABLE TENNIS TRAINER — FastAPI Brain            ║
║        IITGN Project | CV <-> FastAPI <-> Phone UI           ║
╚══════════════════════════════════════════════════════════════╝

Architecture:
  [CV Process] ──SHM──▶ [Poller @ 60Hz] ──▶ [DrillSession] ──▶ [WebSocket Broadcast]
                                                                       │
                                                              [Phone UI clients]

Run:
  pip install fastapi uvicorn websockets
  uvicorn tt_trainer_backend:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import random
import struct
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from multiprocessing.shared_memory import SharedMemory
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

# ─────────────────────────────────────────────
#  CONSTANTS & CONFIG
# ─────────────────────────────────────────────

SHM_NAME        = "tt_cv_bridge"
SHM_SIZE        = 32          # bytes — plenty for our struct + future fields
POLL_HZ         = 60          # CV polling frequency
POLL_INTERVAL   = 1.0 / POLL_HZ

# Struct layout (little-endian):
#   hit_recorded : uint8   (offset 0)  — latch: CV sets 1, API clears to 0
#   success      : uint8   (offset 1)  — 1 = hit target, 0 = miss
#   impact_y     : uint16  (offset 2)  — ball impact Y coordinate (px)
#   impact_z     : uint16  (offset 4)  — ball impact Z coordinate (px)
#   target_zone  : uint8   (offset 6)  — zone index that was targeted
STRUCT_FMT    = "<BBHHBx"   # 8 bytes; trailing 'x' for alignment padding
STRUCT_SIZE   = struct.calcsize(STRUCT_FMT)  # should be 8

# ─────────────────────────────────────────────
#  DIFFICULTY LEVELS
# ─────────────────────────────────────────────

LEVEL_CONFIG: dict[str, dict] = {
    "beginner": {
        "hit_prob":       0.75,   # probability a shot is a success
        "shot_interval":  2.5,    # seconds between simulated shots
        "target_scatter": 30,     # ± pixel scatter around zone centre
        "zones":          3,      # number of distinct target zones
        "description":    "Slow pace, large targets, forgiving accuracy",
    },
    "intermediate": {
        "hit_prob":       0.55,
        "shot_interval":  1.5,
        "target_scatter": 60,
        "zones":          5,
        "description":    "Moderate pace, tighter zones, more variation",
    },
    "advanced": {
        "hit_prob":       0.35,
        "shot_interval":  0.8,
        "target_scatter": 100,
        "zones":          7,
        "description":    "Fast pace, small targets, maximum scatter",
    },
}

# ─────────────────────────────────────────────
#  DRILL SESSION — stateful shot tracker
# ─────────────────────────────────────────────

@dataclass
class DrillSession:
    level: str = "beginner"
    hits: int = 0
    misses: int = 0
    current_streak: int = 0
    best_streak: int = 0
    last_shot_ts: float = field(default_factory=time.time)
    start_ts: float = field(default_factory=time.time)

    # last shot details (populated after each shot)
    last_impact_y: int = 0
    last_impact_z: int = 0
    last_target_zone: int = 0

    @property
    def total_shots(self) -> int:
        return self.hits + self.misses

    @property
    def accuracy_percentage(self) -> float:
        if self.total_shots == 0:
            return 0.0
        return round(self.hits / self.total_shots * 100, 1)

    @property
    def elapsed_seconds(self) -> float:
        return round(time.time() - self.start_ts, 1)

    def record_shot(
        self,
        success: bool,
        impact_y: int,
        impact_z: int,
        target_zone: int,
    ) -> dict:
        """Record one shot; returns a broadcast-ready event dict."""
        self.last_shot_ts   = time.time()
        self.last_impact_y  = impact_y
        self.last_impact_z  = impact_z
        self.last_target_zone = target_zone

        if success:
            self.hits          += 1
            self.current_streak += 1
            self.best_streak    = max(self.best_streak, self.current_streak)
        else:
            self.misses        += 1
            self.current_streak = 0

        streak_bonus = self.current_streak >= 5  # 🔥 hot-streak flag

        event = {
            "event":           "shot_result",
            "success":         success,
            "impact_y":        impact_y,
            "impact_z":        impact_z,
            "target_zone":     target_zone,
            "hits":            self.hits,
            "misses":          self.misses,
            "total_shots":     self.total_shots,
            "accuracy":        self.accuracy_percentage,
            "current_streak":  self.current_streak,
            "best_streak":     self.best_streak,
            "hot_streak":      streak_bonus,
            "elapsed_seconds": self.elapsed_seconds,
        }
        return event

    def to_dict(self) -> dict:
        return {
            "level":           self.level,
            "hits":            self.hits,
            "misses":          self.misses,
            "total_shots":     self.total_shots,
            "accuracy":        self.accuracy_percentage,
            "current_streak":  self.current_streak,
            "best_streak":     self.best_streak,
            "elapsed_seconds": self.elapsed_seconds,
            "last_impact_y":   self.last_impact_y,
            "last_impact_z":   self.last_impact_z,
            "last_target_zone":self.last_target_zone,
            "level_config":    LEVEL_CONFIG[self.level],
        }

    def reset(self, level: Optional[str] = None):
        if level and level in LEVEL_CONFIG:
            self.level = level
        self.hits = self.misses = self.current_streak = self.best_streak = 0
        self.start_ts = time.time()
        print(f"  🔄  Session reset — level: {self.level.upper()}")


# ─────────────────────────────────────────────
#  WEBSOCKET CONNECTION MANAGER
# ─────────────────────────────────────────────

class ConnectionManager:
    """Manages all active Phone UI WebSocket connections."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        print(f"  📱  Phone connected  — total clients: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        print(f"  📴  Phone disconnected — total clients: {len(self.active)}")

    async def broadcast(self, payload: dict):
        """Send JSON payload to every connected client; drop dead connections."""
        if not self.active:
            return
        msg = json.dumps(payload)
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)

    async def send_personal(self, ws: WebSocket, payload: dict):
        await ws.send_text(json.dumps(payload))


# ─────────────────────────────────────────────
#  GLOBAL STATE  (module-level singletons)
# ─────────────────────────────────────────────

manager  = ConnectionManager()
session  = DrillSession()
shm: Optional[SharedMemory] = None   # set during lifespan startup

# ─────────────────────────────────────────────
#  SHARED MEMORY HELPERS
# ─────────────────────────────────────────────

def shm_read() -> tuple[int, int, int, int, int]:
    """Read the CV struct from shared memory.
    Returns (hit_recorded, success, impact_y, impact_z, target_zone).
    """
    raw = bytes(shm.buf[:STRUCT_SIZE])
    return struct.unpack_from(STRUCT_FMT, raw)


def shm_clear_latch():
    """Acknowledge the hit by setting hit_recorded = 0."""
    struct.pack_into("<B", shm.buf, 0, 0)


def shm_inject(
    success: int,
    impact_y: int,
    impact_z: int,
    target_zone: int,
):
    """Write a hit into shared memory (used by Mock CV Simulator & /inject_shot)."""
    packed = struct.pack(STRUCT_FMT, 1, success, impact_y, impact_z, target_zone)
    shm.buf[:STRUCT_SIZE] = packed


# ─────────────────────────────────────────────
#  ASYNC BACKGROUND POLLER  (60 Hz)
# ─────────────────────────────────────────────

async def shm_poller():
    """Poll shared memory at 60 Hz; process hits as they arrive."""
    print("  🔁  SHM Poller started @ 60 Hz — watching for CV hits …")
    while True:
        try:
            hit_recorded, success, impact_y, impact_z, target_zone = shm_read()

            if hit_recorded == 1:
                # --- Acknowledge FIRST to minimise double-reads ---
                shm_clear_latch()

                # --- Update session ---
                event = session.record_shot(
                    success=bool(success),
                    impact_y=impact_y,
                    impact_z=impact_z,
                    target_zone=target_zone,
                )

                # --- Broadcast to phones ---
                await manager.broadcast(event)

                # --- Terminal vibe ---
                icon = "✅" if success else "❌"
                print(
                    f"  {icon}  Shot #{session.total_shots:>4} | "
                    f"zone={target_zone} y={impact_y} z={impact_z} | "
                    f"acc={session.accuracy_percentage}% | "
                    f"streak={session.current_streak}"
                    + (" 🔥" if event["hot_streak"] else "")
                )

        except Exception as exc:
            print(f"  ⚠️   Poller error: {exc}")

        await asyncio.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────
#  MOCK CV SIMULATOR  (dev mode)
# ─────────────────────────────────────────────

async def mock_cv_simulator(level: str = "beginner"):
    """
    Simulates the CV process during development.
    Randomly injects hits into shared memory at the level's shot_interval.
    """
    cfg = LEVEL_CONFIG.get(level, LEVEL_CONFIG["beginner"])
    print(
        f"  🏓  Mock CV Simulator running — level: {level.upper()} | "
        f"interval: {cfg['shot_interval']}s | hit_prob: {cfg['hit_prob']}"
    )

    while True:
        await asyncio.sleep(cfg["shot_interval"] + random.uniform(-0.1, 0.1))

        # Only inject if the latch is clear (don't overwrite unread hit)
        hit_recorded, *_ = shm_read()
        if hit_recorded == 1:
            continue  # poller hasn't consumed the last hit yet

        success      = int(random.random() < cfg["hit_prob"])
        target_zone  = random.randint(0, cfg["zones"] - 1)
        centre_y     = 200 + target_zone * 50
        centre_z     = 150 + target_zone * 30
        scatter      = cfg["target_scatter"]
        impact_y     = max(0, min(65535, centre_y + random.randint(-scatter, scatter)))
        impact_z     = max(0, min(65535, centre_z + random.randint(-scatter, scatter)))

        shm_inject(success, impact_y, impact_z, target_zone)


# ─────────────────────────────────────────────
#  APP LIFESPAN  (startup / shutdown)
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create SHM, launch background tasks, clean up on exit."""
    global shm

    # ── STARTUP ──────────────────────────────
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   🏓  TT Trainer Brain — STARTING UP     ║")
    print("╚══════════════════════════════════════════╝")

    # Create (or attach to existing) shared memory block
    try:
        shm = SharedMemory(name=SHM_NAME, create=True, size=SHM_SIZE)
        # Zero-initialise
        shm.buf[:SHM_SIZE] = bytes(SHM_SIZE)
        print(f"  🧠  SHM created  — name='{SHM_NAME}'  size={SHM_SIZE}B")
    except FileExistsError:
        shm = SharedMemory(name=SHM_NAME, create=False, size=SHM_SIZE)
        print(f"  🧠  SHM attached — name='{SHM_NAME}'  size={SHM_SIZE}B")

    # Launch 60 Hz poller
    poller_task = asyncio.create_task(shm_poller())

    # Launch Mock CV Simulator (dev mode — comment out in production)
    simulator_task = asyncio.create_task(
        mock_cv_simulator(level=session.level)
    )

    print("  🚀  All systems go — API ready\n")
    yield  # ← app runs here

    # ── SHUTDOWN ─────────────────────────────
    print("\n  🛑  Shutting down …")
    poller_task.cancel()
    simulator_task.cancel()

    try:
        shm.close()
        shm.unlink()
        print(f"  🗑️   SHM '{SHM_NAME}' unlinked")
    except Exception as exc:
        print(f"  ⚠️   SHM cleanup warning: {exc}")

    print("  👋  TT Trainer Brain stopped\n")


# ─────────────────────────────────────────────
#  FASTAPI APP
# ─────────────────────────────────────────────

app = FastAPI(
    title="Smart Table Tennis Trainer — Brain API",
    description="CV ↔ FastAPI ↔ Phone UI bridge for the IITGN TT Trainer project",
    version="1.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
#  REST ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Heartbeat endpoint — confirms the brain is alive."""
    return {
        "status":         "ok",
        "shm_attached":   shm is not None,
        "connected_phones": len(manager.active),
        "session_shots":  session.total_shots,
        "uptime_seconds": session.elapsed_seconds,
    }


@app.get("/api/session")
async def get_session():
    """Return the full current drill session stats."""
    return session.to_dict()


@app.post("/api/session/reset")
async def reset_session(level: str = "beginner"):
    """Reset the drill session (optionally change level)."""
    if level not in LEVEL_CONFIG:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown level '{level}'. Choose from: {list(LEVEL_CONFIG)}"},
        )
    session.reset(level=level)
    await manager.broadcast({"event": "session_reset", "level": level})
    return {"status": "reset", "level": level}


@app.post("/api/inject_shot")
async def inject_shot(
    success: int = 1,
    impact_y: int = 200,
    impact_z: int = 150,
    target_zone: int = 0,
):
    """
    Manually inject a shot into shared memory for testing.
    The poller will pick this up within ~17 ms (next 60 Hz tick).

    Params (query string):
      success     : 1 = hit, 0 = miss
      impact_y    : Y coord of ball impact (0–65535)
      impact_z    : Z coord of ball impact (0–65535)
      target_zone : target zone index
    """
    if shm is None:
        return JSONResponse(status_code=503, content={"error": "SHM not ready"})

    # Check latch — warn if previous hit wasn't consumed yet
    hit_recorded, *_ = shm_read()
    if hit_recorded == 1:
        return JSONResponse(
            status_code=409,
            content={"error": "Previous hit not yet consumed by poller — try again in ~17ms"},
        )

    shm_inject(success, impact_y, impact_z, target_zone)
    print(
        f"  💉  Manual inject — success={success} y={impact_y} "
        f"z={impact_z} zone={target_zone}"
    )
    return {
        "status":      "injected",
        "success":     success,
        "impact_y":    impact_y,
        "impact_z":    impact_z,
        "target_zone": target_zone,
        "note":        "Poller will process this within 17ms",
    }


@app.get("/api/levels")
async def get_levels():
    """Return all difficulty level configurations."""
    return LEVEL_CONFIG


# ─────────────────────────────────────────────
#  WEBSOCKET ENDPOINT
# ─────────────────────────────────────────────

@app.websocket("/ws/phone")
async def websocket_phone(ws: WebSocket):
    """
    Phone UI connects here to receive real-time shot events.

    Events sent by server:
      { "event": "shot_result",  "success": bool, "accuracy": float, … }
      { "event": "session_reset", "level": str }
      { "event": "welcome",       "session": { … } }

    Messages accepted from phone:
      { "action": "reset", "level": "beginner" }
      { "action": "ping" }
    """
    await manager.connect(ws)
    try:
        # Send current session state as welcome packet
        await manager.send_personal(ws, {
            "event":   "welcome",
            "message": "🏓 Connected to TT Trainer Brain",
            "session": session.to_dict(),
        })

        # Listen for commands from the phone
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
                action = msg.get("action")

                if action == "ping":
                    await manager.send_personal(ws, {"event": "pong"})

                elif action == "reset":
                    level = msg.get("level", session.level)
                    if level in LEVEL_CONFIG:
                        session.reset(level=level)
                        await manager.broadcast({"event": "session_reset", "level": level})
                    else:
                        await manager.send_personal(ws, {
                            "event": "error",
                            "message": f"Unknown level: {level}",
                        })

                else:
                    await manager.send_personal(ws, {
                        "event":   "error",
                        "message": f"Unknown action: {action}",
                    })

            except json.JSONDecodeError:
                await manager.send_personal(ws, {
                    "event": "error", "message": "Invalid JSON",
                })

    except WebSocketDisconnect:
        manager.disconnect(ws)


# ─────────────────────────────────────────────
#  ENTRY POINT  (optional: run directly)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "tt_trainer_backend:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="warning",   # suppress uvicorn noise; our prints do the job
    )
