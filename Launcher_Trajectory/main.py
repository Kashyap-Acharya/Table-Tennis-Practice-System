import time
import serial
import random
import math
from optimizer import find_launch_parameters, TargetUnreachableError
from kinematics import calculate_motor_rpms

# ==========================================
# 1. THE 20-POINT GRID DEFINITION (From Sketch)
# ==========================================
# Generates a list of 20 dicts: [{"depth": 29, "width": 26.25}, ...]
# Indices 0-19 correspond to specific spots on the user's side of the table.
GRID_POINTS = []
for d in [29, 56, 83, 110]:
    for w in [26.25, 51.25, 76.25, 101.25, 126.25]:
        GRID_POINTS.append({"depth": d, "width": w})

# ==========================================
# 2. THE DRILL DICTIONARY (Grid Based)
# ==========================================
DRILL_DICT = {
    # Forehand (Right side of table from user perspective)
    1: { "name": "Forehand Normal", "valid_points": [3, 4, 8, 9, 13, 14, 18, 19], "v_ms": (8, 11),  "sidespin_rpm": (0, 0), "topspin_rpm": (300, 800) },
    
    # Backhand (Left side of table from user perspective)
    2: { "name": "Backhand Normal", "valid_points": [0, 1, 5, 6, 10, 11, 15, 16], "v_ms": (7, 9),   "sidespin_rpm": (0, 0), "topspin_rpm": (300, 800) },
    
    # Spin Drills
    3: { "name": "Spin (Forehand)", "valid_points": [2, 3, 4, 7, 8, 9, 12, 13, 14], "v_ms": (8, 12),  "sidespin_rpm": (-1500, 1500), "topspin_rpm": (-2500, 2500) },
    4: { "name": "Spin (Backhand)", "valid_points": [0, 1, 2, 5, 6, 7, 10, 11, 12], "v_ms": (8, 12),  "sidespin_rpm": (-1500, 1500), "topspin_rpm": (-2500, 2500) },
    
    # Random (Can hit any of the 20 points)
    5: { "name": "Random (Safe)",   "valid_points": list(range(20)), "v_ms": (10, 15), "sidespin_rpm": (-1000, 1000), "topspin_rpm": (-1500, 1500) }
}

def generate_grid_shot(drill_id):
    """
    Picks a random valid grid point for the drill, applies the Global-to-Local
    Coordinate Translation, and safely scales human units to physics units.
    """
    if drill_id not in DRILL_DICT:
        drill_id = 1
        
    drill = DRILL_DICT[drill_id]
    
    # 1. Pick a discrete grid point
    target_index = random.choice(drill["valid_points"])
    grid_target = GRID_POINTS[target_index]
    
    # 2. Randomize velocities within the drill's bounds
    V = random.uniform(*drill["v_ms"])
    raw_sidespin = random.uniform(*drill["sidespin_rpm"])
    raw_topspin = random.uniform(*drill["topspin_rpm"])
    
    # 3. GLOBAL -> LOCAL FRAME TRANSLATION
    # ----------------------------------------------------
    # UI Frame (Grid): 0 is the Net. X is Width. Y is Depth.
    # Math Frame (Aerodynamics): 0 is Launcher. +X is Depth. +Y is Lateral Left.
    
    # Shift Width: Center of the 152.5cm table is 76.25. 
    target_Y = (76.25 - grid_target["width"]) / 100.0 
    
    # Shift Depth: The robot nozzle is physically mounted 137cm behind the net.
    target_X = (137.0 + grid_target["depth"]) / 100.0 
    # ----------------------------------------------------
    
    # 4. RPM to rad/s
    w1 = raw_topspin * (2 * math.pi) / 60.0    
    w2 = raw_sidespin * (2 * math.pi) / 60.0   
    
    print(f"  -> Generated [{drill['name']}]: Grid Point #{target_index} (Math Target: X={target_X:.2f}m, Y={target_Y:.2f}m)")
    return target_X, target_Y, V, w1, w2

# ==========================================
# 3. THE LAUNCHER ENGINE PROCESS
# ==========================================
def run_launcher_engine(command_queue, serial_port='/dev/ttyUSB0', baud_rate=921600):
    print("[Launcher Engine] Booting up...")
    
    try:
        esp32 = serial.Serial(serial_port, baud_rate, timeout=1)
        print("[Launcher Engine] UART connected to ESP32.")
    except serial.SerialException:
        print("[Launcher Engine] WARNING: ESP32 not found. Running in simulation mode.")
        esp32 = None

    print("[Launcher Engine] Sleeping and waiting for drill commands...")

    while True:
        # Block until Web Server drops an integer
        drill_id = command_queue.get()
        print(f"\n[Launcher Engine] Received Drill ID: {drill_id}")
        
        target_X, target_Y, V, w1, w2 = generate_grid_shot(drill_id)

        try:
            # Feed the perfectly translated coordinates into the Math Engine
            pitch, yaw = find_launch_parameters(target_X, target_Y, V, w1, w2)
            m1, m2, m3 = calculate_motor_rpms(V, w1, w2)
            
            # Format and Fire
            uart_string = f"M:{m1}:{m2}:{m3}\nA:{pitch:.1f}:{yaw:.1f}\nH:{drill_id}\n"
            
            if esp32:
                esp32.write(uart_string.encode('ascii'))
                print(f"[Launcher Engine] Fired to ESP32: {uart_string.strip()}")
            else:
                print(f"[Launcher Engine] SIMULATED OUTPUT: {uart_string.strip()}")
                
        except TargetUnreachableError as e:
            print(f"[Launcher Engine] MATH ERROR: {e}")
