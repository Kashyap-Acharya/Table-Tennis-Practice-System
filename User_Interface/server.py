"""
WOE Robot — FastAPI Web Server (Phase 1)
=========================================
Brain 1 (Pi 4B) web layer.

The phone sends { "drill_id": <int> }.
This server maps it to the full DrillCommand and prints it to the terminal.

Phase 2 will write the resolved command into the Shared Memory Queue.
Phase 3 will fire the serial string to the Pico over USB.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dataclasses import dataclass
from typing import Optional, Dict, Any
from pathlib import Path
import uvicorn

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="WOE Robot Server", version="0.1.0")

# Allow requests from any origin on the local network.
# Without this the browser will silently block the phone's POST.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resolve index.html relative to this file, not the working directory.
# os.path.dirname(__file__) can return "" when uvicorn loads the module
# by string ("server:app"), which breaks if CWD is not the script folder.
INDEX_PATH = Path(__file__).parent.resolve() / "index.html"

# ---------------------------------------------------------------------------
# Drill Library
# ---------------------------------------------------------------------------
# Each entry maps a drill number (what the phone sends) to the full set of
# hardware parameters the Pico needs.
#
# ┌─────────────────────────────────────────────────────────────────────┐
# │  launcher_speed : PWM / RPM value sent as  L:<value>\n             │
# │  servo_tilt     : vertical aiming angle    A:<tilt>:<yaw>\n        │
# │  servo_yaw      : horizontal aiming angle                          │
# │  target_zone    : LED zone to light up     H:<zone>\n              │
# │  name           : human-readable label for logs                    │
# │  description    : shown in the terminal printout                   │
# └─────────────────────────────────────────────────────────────────────┘
#
# NOTE: field is called `name` (not `drill_id`) to avoid confusion with
#       the int key used to look up the preset (request.drill_id).
#
#   TODO (Hardware team): Replace placeholder values with real
#     calibrated numbers once the launcher and servos are benchmarked.

@dataclass(frozen=True)
class DrillPreset:
    name: str               # human-readable label, e.g. "fh_topspin_zone5"
    description: str
    target_zone: int        # 1–9
    launcher_speed: int     # 0–255  (calibrate against actual RPM)
    servo_tilt: int         # degrees, vertical
    servo_yaw: int          # degrees, horizontal (negative = left)


DRILL_LIBRARY: dict[int, DrillPreset] = {
    # ── Forehand drills ────────────────────────────────────────────────
    1: DrillPreset(
        name="fh_topspin_zone5",
        description="Forehand Topspin → Zone 5 (centre)",
        target_zone=5,
        launcher_speed=180,
        servo_tilt=45,
        servo_yaw=0,
    ),
    2: DrillPreset(
        name="fh_topspin_zone3",
        description="Forehand Topspin → Zone 3 (right)",
        target_zone=3,
        launcher_speed=180,
        servo_tilt=45,
        servo_yaw=12,
    ),
    3: DrillPreset(
        name="fh_topspin_zone7",
        description="Forehand Topspin → Zone 7 (left)",
        target_zone=7,
        launcher_speed=180,
        servo_tilt=45,
        servo_yaw=-12,
    ),
    # ── Backhand drills ────────────────────────────────────────────────
    4: DrillPreset(
        name="bh_drive_zone5",
        description="Backhand Drive → Zone 5 (centre)",
        target_zone=5,
        launcher_speed=160,
        servo_tilt=40,
        servo_yaw=0,
    ),
    5: DrillPreset(
        name="bh_drive_zone1",
        description="Backhand Drive → Zone 1 (top-left)",
        target_zone=1,
        launcher_speed=160,
        servo_tilt=50,
        servo_yaw=-15,
    ),
    # ── Smash / fast drills ────────────────────────────────────────────
    6: DrillPreset(
        name="smash_zone2",
        description="Overhead Smash → Zone 2 (top-centre)",
        target_zone=2,
        launcher_speed=220,
        servo_tilt=60,
        servo_yaw=0,
    ),
    7: DrillPreset(
        name="smash_zone8",
        description="Overhead Smash → Zone 8 (bottom-centre)",
        target_zone=8,
        launcher_speed=220,
        servo_tilt=30,
        servo_yaw=0,
    ),
    # ── Slow / placement drills ────────────────────────────────────────
    8: DrillPreset(
        name="push_zone4",
        description="Backspin Push → Zone 4 (centre-left)",
        target_zone=4,
        launcher_speed=120,
        servo_tilt=35,
        servo_yaw=-8,
    ),
    9: DrillPreset(
        name="push_zone6",
        description="Backspin Push → Zone 6 (centre-right)",
        target_zone=6,
        launcher_speed=120,
        servo_tilt=35,
        servo_yaw=8,
    ),
}

# ---------------------------------------------------------------------------
# Request model — matches the UI contract exactly.
# drill_id is an int because the UI casts it with Number() before sending.
# Field(default_factory=dict) is used instead of = {} to avoid the shared
# mutable default bug present in Pydantic v1 (common on Pi OS images).
# ---------------------------------------------------------------------------

class CommandPayload(BaseModel):
    drill_id: int
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Route 1 — Serve the frontend
# ---------------------------------------------------------------------------

@app.get("/", summary="Serve the phone UI")
async def serve_ui():
    if not INDEX_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="index.html not found — place it alongside server.py.",
        )
    return FileResponse(str(INDEX_PATH), media_type="text/html")


# ---------------------------------------------------------------------------
# Route 2 — Receive drill_id, resolve full command, print to terminal
# ---------------------------------------------------------------------------

@app.post("/api/command", summary="Receive drill_id and resolve parameters")
async def receive_command(request: CommandPayload):
    """
    1. Phone sends  { "drill_id": 3 }  (drill_id is an int, cast via Number() in JS)
    2. Server looks up the full preset from DRILL_LIBRARY
    3. Prints the resolved parameters to the terminal (Phase 1 proof)
    4. Returns the resolved command + serial string back to the phone

    Phase 2 (TODO): write resolved command into Shared Memory Queue.
    Phase 3 (TODO): fire serial string to Pico at 921,600 baud.
    """

    drill = DRILL_LIBRARY.get(request.drill_id)

    if drill is None:
        valid = sorted(DRILL_LIBRARY.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Drill #{request.drill_id} not found. Valid drills: {valid}",
        )

    # Build the serial string the Pico will eventually receive
    serial_string = (
        f"L:{drill.launcher_speed}\n"
        f"A:{drill.servo_tilt}:{drill.servo_yaw}\n"
        f"H:{drill.target_zone}\n"
    )

    # ------------------------------------------------------------------
    # Phase 1 — terminal proof-of-connection
    # ------------------------------------------------------------------
    print("\n" + "=" * 55)
    print(f"  📡  DRILL #{request.drill_id} RECEIVED FROM PHONE")
    print("=" * 55)
    print(f"  Name           : {drill.name}")
    print(f"  Description    : {drill.description}")
    print(f"  Target Zone    : {drill.target_zone}")
    print(f"  Launcher Speed : {drill.launcher_speed}")
    print(f"  Servo Tilt     : {drill.servo_tilt}°")
    print(f"  Servo Yaw      : {drill.servo_yaw}°")
    print(f"  Serial String  : {repr(serial_string)}")
    if request.parameters:
        print(f"  Extra Params   : {request.parameters}")
    print("=" * 55 + "\n")

    # ------------------------------------------------------------------
    # Phase 2 placeholder — Shared Memory Queue write will go here
    # ------------------------------------------------------------------
    # from shared_memory_queue import enqueue
    # enqueue({
    #     "current_target": drill.target_zone,
    #     "status": "waiting_for_hit",
    # })

    # ------------------------------------------------------------------
    # Phase 3 placeholder — Serial write to Pico will go here
    # ------------------------------------------------------------------
    # import serial
    # with serial.Serial("/dev/ttyACM0", 921600, timeout=1) as pico:
    #     pico.write(serial_string.encode("ascii"))

    return JSONResponse(
        status_code=200,
        content={
            "status": "received",
            "drill_id": request.drill_id,
            "resolved": {
                "name": drill.name,
                "description": drill.description,
                "target_zone": drill.target_zone,
                "launcher_speed": drill.launcher_speed,
                "servo_tilt": drill.servo_tilt,
                "servo_yaw": drill.servo_yaw,
            },
            "serial_string": serial_string,
        },
    )


# ---------------------------------------------------------------------------
# Route 3 — List all available drills (handy for debugging / future UI)
# ---------------------------------------------------------------------------

@app.get("/api/drills", summary="List all available drills")
async def list_drills():
    return {
        num: {
            "name": d.name,
            "description": d.description,
            "target_zone": d.target_zone,
        }
        for num, d in sorted(DRILL_LIBRARY.items())
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",   # All interfaces — phones on the hotspot can reach it
        port=8000,
        reload=False,
        log_level="info",
    )
