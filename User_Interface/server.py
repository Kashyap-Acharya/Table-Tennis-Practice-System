import asyncio, json, random, uuid
from pathlib import Path
from dataclasses import dataclass, field
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

@dataclass
class TrainerState:
    active: bool = False
    paused: bool = False
    hits: int = 0
    shots: int = 0
    streak: int = 0
    accuracy: int = 0
    history: list = field(default_factory=list)

state = TrainerState()
BASE_DIR = Path(__file__).resolve().parent

class QueueManager:
    def __init__(self):
        self.users = [] # {"uid": str, "ws": WebSocket, "grace_task": Task}

    async def connect(self, ws: WebSocket, uid: str):
        await ws.accept()
        existing_user = next((u for u in self.users if u["uid"] == uid), None)
        
        if existing_user:
            if existing_user["grace_task"]:
                existing_user["grace_task"].cancel()
                existing_user["grace_task"] = None
            existing_user["ws"] = ws
        else:
            self.users.append({"uid": uid, "ws": ws, "grace_task": None})
        
        await ws.send_json({
            "event": "session_sync", "active": state.active, "paused": state.paused, 
            "accuracy": state.accuracy, "total_shots": state.shots, 
            "streak": state.streak, "history": state.history
        })
        await self.sync_queue()

    def disconnect(self, uid: str):
        for user in self.users:
            if user["uid"] == uid:
                user["grace_task"] = asyncio.create_task(self.evict_after_delay(uid))
                break

    async def evict_after_delay(self, uid):
        try:
            await asyncio.sleep(5) 
            
            # CHECK: Was this user the leader?
            is_leader_leaving = len(self.users) > 0 and self.users[0]["uid"] == uid
            
            self.users = [u for u in self.users if u["uid"] != uid]
            
            # IF LEADER LEFT: Stop the drill immediately
            if is_leader_leaving:
                state.active = False
                state.history = []
                await self.broadcast({"event": "drill_stopped"})
                print(f"AUTO-STOP: Leader {uid} timed out. Stopping drill.")

            await self.sync_queue()
        except asyncio.CancelledError:
            pass

    async def sync_queue(self):
        for i, u in enumerate(self.users):
            if u["ws"]:
                try: await u["ws"].send_json({"event": "queue_update", "position": i + 1, "total": len(self.users), "is_leader": i == 0})
                except: pass

    async def broadcast(self, data):
        for u in self.users:
            if u["ws"]:
                try: await u["ws"].send_json(data)
                except: pass

    def is_leader(self, uid): 
        return len(self.users) > 0 and self.users[0]["uid"] == uid

q = QueueManager()

async def telemetry_engine():
    while True:
        if state.active and not state.paused:
            success = random.random() > 0.4
            coords = {"x": random.randint(450, 1450), "y": random.randint(250, 750)}
            state.shots += 1
            if success: state.hits += 1; state.streak += 1
            else: state.streak = 0
            state.accuracy = int((state.hits / max(1, state.shots)) * 100)
            state.history.append({"x": coords["x"], "y": coords["y"], "success": success})
            if len(state.history) > 50: state.history.pop(0)
            await q.broadcast({"event": "shot_result", "success": success, "accuracy": state.accuracy, "total_shots": state.shots, "streak": state.streak, "impact_coords": coords})
        await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(telemetry_engine())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.get("/api/session")
async def get_session():
    return { "active": state.active, "paused": state.paused, "accuracy": state.accuracy, "total_shots": state.shots, "streak": state.streak, "history": state.history }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return Response(status_code=204)

@app.get("/")
async def get_index(): return FileResponse(BASE_DIR / "index.html")
@app.get("/styles.css")
async def get_css(): return FileResponse(BASE_DIR / "styles.css")
@app.get("/dashboard.js")
async def get_js(): return FileResponse(BASE_DIR / "dashboard.js")

@app.websocket("/ws/{uid}")
async def websocket_endpoint(ws: WebSocket, uid: str):
    await q.connect(ws, uid)
    try:
        while True:
            data = await ws.receive_json()
            if not q.is_leader(uid): continue 
            action = data.get("action")
            if action == "start_drill":
                state.active, state.paused, state.hits, state.shots, state.streak, state.history = True, False, 0, 0, 0, []
                await q.broadcast({"event": "drill_started"})
            elif action == "pause_drill":
                state.paused = not state.paused
                await q.broadcast({"event": "drill_paused", "paused": state.paused})
            elif action == "stop_drill":
                state.active = False
                await q.broadcast({"event": "drill_stopped"})
    except WebSocketDisconnect:
        q.disconnect(uid)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
