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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

# --- Configuration ---
SHM_NAME = "tt_cv_bridge"
SHM_FMT = "<B B H H B 3x 22x"
SHM_SIZE = struct.calcsize(SHM_FMT)
LEVEL_CONFIG = {
    "BEG": {"interval": (3.0, 5.0), "hit_prob": 0.80, "scatter_mm": 80},
    "INT": {"interval": (2.0, 3.5), "hit_prob": 0.65, "scatter_mm": 60},
    "ADV": {"interval": (1.5, 2.5), "hit_prob": 0.50, "scatter_mm": 40},
}

@dataclass
class DrillSession:
    drill_id: str = ""
    active: bool = False
    hits: int = 0
    misses: int = 0
    current_streak: int = 0
    best_streak: int = 0
    start_ts: float = field(default_factory=time.time)

    def reset(self):
        self.hits = self.misses = self.current_streak = self.best_streak = 0
        self.start_ts = time.time()

    @property
    def total_shots(self): return self.hits + self.misses

    @property
    def accuracy(self): return int(round(self.hits / max(1, self.total_shots) * 100, 0))

session = DrillSession()

# --- Connection Manager ---
class ConnectionManager:
    def __init__(self): self.active: list[WebSocket] = []
    async def connect(self, ws: WebSocket): await ws.accept(); self.active.append(ws)
    def disconnect(self, ws: WebSocket): self.active.remove(ws) if ws in self.active else None
    async def broadcast(self, payload: dict):
        for ws in self.active:
            try: await ws.send_json(payload)
            except: pass

manager = ConnectionManager()

# --- FastAPI App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mock loop for demo purposes
    async def mock_loop():
        while True:
            if session.active:
                success = random.random() < 0.7
                y, z = random.randint(-762, 762), random.randint(0, 1000)
                if success: session.hits += 1; session.current_streak += 1
                else: session.misses += 1; session.current_streak = 0
                session.best_streak = max(session.best_streak, session.current_streak)
                
                await manager.broadcast({
                    "event": "shot_result",
                    "success": success,
                    "impact_coords": {"y": y, "z": z},
                    "accuracy": session.accuracy,
                    "hit_count": session.hits,
                    "total_shots": session.total_shots
                })
            await asyncio.sleep(3)
    
    task = asyncio.create_task(mock_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.get("/")
async def get_index(): return FileResponse("index.html")

@app.get("/{file_name}")
async def get_static(file_name: str): return FileResponse(file_name)

@app.get("/api/session")
async def get_session():
    return {
        "active": session.active,
        "total_shots": session.total_shots,
        "hit_count": session.hits,
        "streak": session.current_streak,
        "accuracy": session.accuracy
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data["action"] == "start_drill":
                session.active = True
                session.reset()
                await manager.broadcast({"event": "drill_started", "drill_id": data["drill_id"]})
            elif data["action"] == "stop_drill":
                session.active = False
                await manager.broadcast({"event": "drill_stopped"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
