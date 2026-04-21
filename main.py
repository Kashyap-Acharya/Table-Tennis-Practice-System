```python

"""

╔══════════════════════════════════════════════════════════════════════════╗

║    IITGN — SMART TABLE TENNIS TRAINER: MASTER ORCHESTRATOR               ║

║    Process Management  ·  IPC Queues  ·  Atomic Locks  ·  SHM Lifecycle  ║

╚══════════════════════════════════════════════════════════════════════════╝

"""



import multiprocessing as mp

import time

import sys

import queue 

import random 

from multiprocessing.shared_memory import SharedMemory



# 🟢 Import your core modules here

try:

    from server import run_web_server

except ImportError:

    print("[System] WARNING: Could not import 'server.py'. Ensure it is in the same directory.")



# ==========================================

# SHARED MEMORY CONFIG

# ==========================================

SHM_NAME = "tt_cv_bridge"

SHM_SIZE = 34  



def initialize_shm():

    """Allocates or attaches to the shared memory segment, then zeros it."""

    try:

        shm = SharedMemory(name=SHM_NAME, create=True, size=SHM_SIZE)

        print(f"[System] Shared Memory '{SHM_NAME}' created ({SHM_SIZE} bytes).")

    except FileExistsError:

        shm = SharedMemory(name=SHM_NAME, create=False, size=SHM_SIZE)

        print(f"[System] WARNING: Stale SHM found. Attaching and zeroing.")

    

    shm.buf[:SHM_SIZE] = b'\x00' * SHM_SIZE

    return shm



def terminate_process(p: mp.Process, timeout: int = 3):

    """Gracefully stops a process, escalates to SIGKILL if necessary."""

    if p is None or not p.is_alive():

        return



    print(f"[System] Stopping {p.name} (PID {p.pid})...")

    p.terminate()

    p.join(timeout=timeout)



    if p.is_alive():

        print(f"[System] {p.name} did not stop. Force killing...")

        p.kill()

        p.join(timeout=2)



    print(f"[System] {p.name} stopped.")



def mock_hardware_launcher(command_queue, feedback_queue):

    """Simulates the ESP32 Launcher Core for software-only testing."""

    print("[Launcher] Hardware Core Booting...")

    

    while True:

        try:

            cmd = command_queue.get(timeout=0.1)

            print(f"[Launcher] Received Command ID: {cmd} from Web UI")

            if cmd == 0:

                print("[Launcher] EMERGENCY STOP triggered by Server!")

        except queue.Empty:

            pass

        

        if random.random() < 0.05: 

            simulated_zone = random.randint(1, 5)

            feedback_queue.put({"active_zone": simulated_zone})

            print(f"[Launcher] Switched LED to Zone {simulated_zone}. Updating Server...")

            

        time.sleep(0.05) 



if __name__ == "__main__":

    print("╔════════════════════════════════════════════╗")

    print("║    🏓 IITGN T4-TRAINER: SYSTEM BOOT        ║")

    print("╚════════════════════════════════════════════╝")



    # 1. Create IPC Queues & The Atomic Lock

    command_queue  = mp.Queue()  

    feedback_queue = mp.Queue()  

    master_lock    = mp.Lock()   # 🟢 RESTORED: The atomic safety belt



    # 2. Allocate Shared Memory

    shm_segment = initialize_shm()



    # 3. Define Processes

    p_server = mp.Process(

        target=run_web_server,

        args=(command_queue, feedback_queue, master_lock), # 🟢 Pass all 3 args!

        name="WebServer",

        daemon=True

    )

    

    p_launcher = mp.Process(

        target=mock_hardware_launcher, 

        args=(command_queue, feedback_queue),

        name="LauncherEngine",

        daemon=True

    )



    processes = [p_server, p_launcher]



    try:

        # 4. Start all processes

        for p in processes:

            p.start()

            print(f"[System] {p.name} online → PID {p.pid}")



        print("\n[System] All processes live on CPU cores. Press Ctrl+C to halt.\n")



        # 5. Watchdog loop

        while True:

            time.sleep(2)

            for p in processes:

                if not p.is_alive():

                    print(f"[System] ⚠️  {p.name} (PID {p.pid}) has DIED unexpectedly!")

                    print(f"[System]    Exit code: {p.exitcode}")



    except KeyboardInterrupt:

        print("\n[System] Ctrl+C received. Initiating shutdown sequence...")



    finally:

        # 6. Clean up processes

        for p in processes:

            terminate_process(p)



        # 7. Release shared memory

        shm_segment.close()

        try:

            shm_segment.unlink()

            print("[System] Shared Memory released.")

        except Exception as e:

            print(f"[System] SHM unlink warning: {e}")



        print("[System] Cleanup complete. Safety Halt engaged.")

        sys.exit(0)

```



**One final critical note for your Web team:** Because we added `master_lock` to the `p_server` args, they must update the wrapper function at the very bottom of `server.py` to accept it, or the signature mismatch crash *will* happen. 



They just need to change the bottom of `server.py` to look like this:

```python

# ==========================================

# 7. EXECUTION WRAPPER (Inside server.py)

# ==========================================

def run_web_server(command_queue, feedback_queue, cross_process_lock):

    app.state.command_queue = command_queue

    app.state.feedback_queue = feedback_queue

    app.state.cv_lock = cross_process_lock # Save the lock for the SHM poller to use!

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
