import time
import serial
import random
import math
from optimizer import find_launch_parameters, TargetUnreachableError
from kinematics import calculate_motor_rpms

# ==========================================
# 1. THE DRILL DICTIONARY (From Spreadsheet)
# ==========================================
# Format: { drill_id: { "depth_cm": (min, max), "width_cm": (min, max), "v_ms": (min, max), "sidespin_rpm": (min, max), "topspin_rpm": (min, max) } }
DRILL_DICT = {
    1: { "name": "Forehand Normal", "depth_cm": (60, 120), "width_cm": (15, 70),   "v_ms": (8, 11),  "sidespin_rpm": (0, 0),         "topspin_rpm": (300, 800) },
    2: { "name": "Backhand Normal", "depth_cm": (60, 120), "width_cm": (82, 137),  "v_ms": (7, 9),   "sidespin_rpm": (0, 0),         "topspin_rpm": (300, 800) },
    3: { "name": "Spin (Forehand)", "depth_cm": (30, 125), "width_cm": (15, 70),   "v_ms": (8, 12),  "sidespin_rpm": (-1500, 1500),  "topspin_rpm": (-2500, 2500) },
    4: { "name": "Spin (Backhand)", "depth_cm": (30, 125), "width_cm": (82, 137),  "v_ms": (8, 12),  "sidespin_rpm": (-1500, 1500),  "topspin_rpm": (-2500, 2500) },
    5: { "name": "Random (Safe)",   "depth_cm": (40, 115), "width_cm": (15, 137),  "v_ms": (10, 15), "sidespin_rpm": (-1000, 1000),  "topspin_rpm": (-1500, 1500) }
}

def generate_randomized_shot(drill_id):
    """
    Pulls the drill ranges, randomizes a value within those bounds, 
    and safely converts human units (cm, RPM) to physics units (m, rad/s).
    """
    if drill_id not in DRILL_DICT:
        print(f"[Warning] Drill {drill_id} not found. Defaulting to 1.")
        drill_id = 1
        
    ranges = DRILL_DICT[drill_id]
    
    # 1. Randomize within bounds
    raw_depth = random.uniform(*ranges["depth_cm"])
    raw_width = random.uniform(*ranges["width_cm"])
    V = random.uniform(*ranges["v_ms"])
    raw_sidespin = random.uniform(*ranges["sidespin_rpm"])
    raw_topspin = random.uniform(*ranges["topspin_rpm"])
    
    # 2. Physics Conversions
    target_X = raw_depth / 100.0 # cm to meters
    
    # Shift width so 0 is the center of the 152.5cm table, then convert to meters
    target_Y = (raw_width - 76.25) / 100.0 
    
    # RPM to Radians per Second (rpm * 2π / 60)
    w1 = raw_topspin * (2 * math.pi) / 60.0    # math team expects w1 = topspin
    w2 = raw_sidespin * (2 * math.pi) / 60.0   # math team expects w2 = sidespin
    
    print(f"  -> Generated [{ranges['name']}]: Depth={target_X:.2f}m, Width={target_Y:.2f}m, V={V:.1f}m/s")
    return target_X, target_Y, V, w1, w2


# ==========================================
# 2. THE LAUNCHER ENGINE PROCESS
# ==========================================
def launcher_engine_process(command_queue, serial_port='/dev/ttyUSB0', baud_rate=921600):
    print("[Launcher Engine] Booting up...")
    
    try:
        esp32 = serial.Serial(serial_port, baud_rate, timeout=1)
        print("[Launcher Engine] UART connected to ESP32.")
    except serial.SerialException:
        print("[Launcher Engine] WARNING: ESP32 not found. Running in simulation mode.")
        esp32 = None

    print("[Launcher Engine] Sleeping and waiting for drill commands...")

    while True:
        # 1. Block and wait for an integer from the Web Server
        drill_id = command_queue.get()
        print(f"\n[Launcher Engine] Received Drill ID: {drill_id}")
        
        # 2. Randomize and convert units
        target_X, target_Y, V, w1, w2 = generate_randomized_shot(drill_id)

        try:
            # 3. Feed the Math Engine
            pitch, yaw = find_launch_parameters(target_X, target_Y, V, w1, w2)
            m1, m2, m3 = calculate_motor_rpms(V, w1, w2)
            
            # 4. Format and Fire (Assuming Zone is tied to drill_id for now)
            uart_string = f"M:{m1}:{m2}:{m3}\nA:{pitch:.1f}:{yaw:.1f}\nH:{drill_id}\n"
            
            if esp32:
                esp32.write(uart_string.encode('ascii'))
                print(f"[Launcher Engine] Fired to ESP32: {uart_string.strip()}")
            else:
                print(f"[Launcher Engine] SIMULATED OUTPUT: {uart_string.strip()}")
                
        except TargetUnreachableError as e:
            # If the randomizer picks an impossible physics combination
            print(f"[Launcher Engine] ERROR: {e}")
