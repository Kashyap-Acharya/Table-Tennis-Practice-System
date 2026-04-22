import asyncio, struct
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from multiprocessing.shared_memory import SharedMemory
from contextlib import asynccontextmanager

BASE_DIR = Path(__file__).resolve().parent
SHM_FMT = "<BBHHB3x22x" # Matches CV exactly
SHM_NAME = "tt_cv_bridge"

class SystemState:
    def __init__(self, cmd_q, feed_q):
        self.cmd_q, self.feed_q = cmd_q, feed_q
        self.users = []
        self.active, self.paused = False, False
        self.shots, self.hits, self.streak = 0, 0, 0
        try: self.shm = SharedMemory(name=SHM_NAME)
        except FileNotFoundError: self.shm = None

    async def broadcast(self, data):
        for u in self.users:
            if u["ws"]:
                try: await u["ws"].send_json(data)
                except: pass

async def hardware_bridge_loop(gs):
    """Exclusively reads from physical hardware queues/memory."""
    while True:
        # 1. Launcher Queue Feed
        while not gs.feed_q.empty():
            data = gs.feed_q.get()
            if "shot_number" in data:
                gs.shots = data["shot_number"]
                if data.get("status") == "failed": gs.streak = 0
            await gs.broadcast({"event": "launcher_update", "data": data})

        # 2. Vision Shared Memory Feed
        if gs.shm and gs.shm.buf[0] == 1:
            raw = gs.shm.buf[:struct.calcsize(SHM_FMT)]
            _, _, y_mm, z_mm, _ = struct.unpack(SHM_FMT, raw)
            
            # Handshake: Tell CV we read the data so it can write the next hit
            gs.shm.buf[0] = 0 
            
            gs.hits += 1
            gs.streak += 1
            acc = int((gs.hits / max(1, gs.shots)) * 100)
            
            await gs.broadcast({
                "event": "shot_result",
                "success": True,
                "accuracy": acc,
                "total_shots": gs.shots,
                "streak": gs.streak,
                "impact_coords": {"x": y_mm, "y": z_mm}
            })
        await asyncio.sleep(0.01)

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(hardware_bridge_loop(app.state.gs))
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.get("/")
async def get_index(): return FileResponse(BASE_DIR / "index.html")
@app.get("/styles.css")
async def get_css(): return FileResponse(BASE_DIR / "styles.css", media_type="text/css")
@app.get("/dashboard.js")
async def get_js(): return FileResponse(BASE_DIR / "dashboard.js", media_type="application/javascript")

@app.websocket("/ws/{uid}")
async def websocket_endpoint(ws: WebSocket, uid: str):
    gs = app.state.gs
    await ws.accept()
    gs.users.append({"uid": uid, "ws": ws})
    try:
        while True:
            data = await ws.receive_json()
            is_leader = len(gs.users) > 0 and gs.users[0]["uid"] == uid
            if not is_leader: continue 
            
            action = data.get("action")
            if action == "start_drill":
                gs.active, gs.paused, gs.hits, gs.shots, gs.streak = True, False, 0, 0, 0
                drill_map = {'fh_normal': 1, 'bh_normal': 2, 'fh_spin': 3, 'bh_spin': 4, 'random': 5}
                drill_id = drill_map.get(data.get("drill_id"), 5)
                gs.cmd_q.put(drill_id)
                await gs.broadcast({"event": "drill_started"})
            elif action == "stop_drill":
                gs.active = False
                gs.cmd_q.put(0) 
                await gs.broadcast({"event": "drill_stopped"})
    except WebSocketDisconnect:
        gs.users = [u for u in gs.users if u["uid"] != uid]

def run_web_server(cmd_q, feed_q):
    import uvicorn
    app.state.gs = SystemState(cmd_q, feed_q)
    uvicorn.run(app, host="0.0.0.0", port=8000)
