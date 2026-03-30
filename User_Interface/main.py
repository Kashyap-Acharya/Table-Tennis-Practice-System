import asyncio
import json
import random
import struct
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from multiprocessing import shared_memory
import uvicorn

# --- GLOBAL DRILL STATE ---
drill_state = {
    "is_running": False,
    "drill_id": None,
    "total_shots": 0,
    "successful_hits": 0,
    "current_streak": 0,
    "max_streak": 0,
    "velocity_log": []
}

active_connections = []
SHM_NAME = "cv_impact_data"

class MockSerial:
    def write(self, data):
        # Simulating sending UART command to Pico
        print(f"[UART TX to PICO] -> {data.decode().strip()}")

pico_serial = MockSerial()

async def read_shared_memory_from_cv():
    try:
        shm = shared_memory.SharedMemory(name=SHM_NAME)
        data = struct.unpack('?ff', shm.buf[:9])
        shm.buf[0] = struct.pack('?', False)
        return {"hit_recorded": data[0], "y": data[1], "z": data[2]}
    except FileNotFoundError:
        # Fallback simulation if CV is not running
        await asyncio.sleep(random.uniform(1.0, 2.5))
        return {
            "hit_recorded": True,
            "y": random.randint(-760, 760), 
            "z": random.randint(0, 1370)    
        }

# --- BACKGROUND HARDWARE LOOP ---
async def cv_polling_loop():
    """Runs 24/7. Survives UI refreshes and errors."""
    while True:
        try:
            if drill_state["is_running"]:
                # Hardware logic/target zones based on drill_state["drill_id"] will go here
                
                cv_data = await read_shared_memory_from_cv()
                
                if cv_data and cv_data.get("hit_recorded"):
                    success = random.choice([True, False])
                    velocity = random.randint(40, 95)
                    
                    drill_state["total_shots"] += 1
                    drill_state["velocity_log"].append(velocity)
                    if success:
                        drill_state["successful_hits"] += 1
                        drill_state["current_streak"] += 1
                        if drill_state["current_streak"] > drill_state["max_streak"]:
                            drill_state["max_streak"] = drill_state["current_streak"]
                    else:
                        drill_state["current_streak"] = 0

                    response = {
                        "event": "shot_result",
                        "success": success,
                        "impact_coords": {"y": int(cv_data["y"]), "z": int(cv_data["z"])},
                        "velocity": velocity
                    }
                    
                    for connection in active_connections:
                        try:
                            await connection.send_text(json.dumps(response))
                        except:
                            pass
            else:
                await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Background Loop Error Prevented Crash: {e}")
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(cv_polling_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            # Browser explicitly asks for state on load/refresh
            if msg.get("action") == "get_state":
                await websocket.send_text(json.dumps({
                    "event": "sync_state",
                    "state": drill_state
                }))
            
            elif msg.get("action") == "start_drill":
                drill_id = msg.get("drill_id")
                
                # --- STRICT VALIDATION ---
                # Only allow the 4 official modes: Forehand (1), Backhand (2), Spin (3), Random (4)
                if drill_id not in [1, 2, 3, 4]:
                    print(f"[WARNING] Rejected invalid drill_id from UI: {drill_id}")
                    continue 
                # -------------------------

                drill_state.update({
                    "is_running": True,
                    "drill_id": drill_id,
                    "total_shots": 0,
                    "successful_hits": 0,
                    "current_streak": 0,
                    "max_streak": 0,
                    "velocity_log": []
                })
                # Send the validated numeric ID to the Pico
                pico_serial.write(f"START:{drill_id}\n".encode())
                print(f"Mode {drill_id} started.")
                
            elif msg.get("action") == "stop_drill":
                drill_state["is_running"] = False
                # Send ID 99 to Pico for Stop/Emergency Stop
                pico_serial.write(b"STOP:99\n")
                print("Drill stopped / Emergency Stop triggered.")
                
    except WebSocketDisconnect:
        active_connections.remove(websocket)

@app.get("/")
async def get_dashboard():
    with open("index.html", "r") as f:
        return HTMLResponse(f.read())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
