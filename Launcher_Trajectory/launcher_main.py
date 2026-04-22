import time
import serial
import random
import math
import queue
from optimizer import find_launch_parameters, TargetUnreachableError
from kinematics import calculate_motor_rpms

# ==========================================
# 1. THE DRILL DICTIONARY
# ==========================================
DRILL_DICT = {
    1: { "name": "Forehand Normal", "depth_cm": (60, 120), "width_cm": (15, 70),  "v_ms": (8, 11), "sidespin_rpm": (0, 0),        "topspin_rpm": (300, 800)  },
    2: { "name": "Backhand Normal", "depth_cm": (60, 120), "width_cm": (82, 137), "v_ms": (7, 9),  "sidespin_rpm": (0, 0),        "topspin_rpm": (300, 800)  },
    3: { "name": "FH Spin",        "depth_cm": (30, 125), "width_cm": (15, 70),  "v_ms": (8, 12), "sidespin_rpm": (-1500, 1500), "topspin_rpm": (-500, 1000)},
    4: { "name": "BH Spin",        "depth_cm": (30, 125), "width_cm": (82, 137), "v_ms": (8, 12), "sidespin_rpm": (-1500, 1500), "topspin_rpm": (-500, 1000)},
    5: { "name": "Random",         "depth_cm": (30, 125), "width_cm": (15, 137), "v_ms": (7, 12), "sidespin_rpm": (-1000, 1000), "topspin_rpm": (-500, 1000)},
}

def generate_randomized_shot(drill_id):
    """Pulls ranges from the dictionary and strictly converts to SI units."""
    drill = DRILL_DICT[drill_id]
    
    # 1. Coordinate Conversion (cm to meters)
    target_X = random.uniform(drill["width_cm"][0], drill["width_cm"][1]) / 100.0
    target_Y = random.uniform(drill["depth_cm"][0], drill["depth_cm"][1]) / 100.0
    
    # 2. Velocity (Already in m/s)
    V = random.uniform(drill["v_ms"][0], drill["v_ms"][1])
    
    # 3. Spin Conversion: rad/s = RPM * (2 * pi / 60)
    rpm_to_rads = (2.0 * math.pi) / 60.0
    w1 = random.uniform(drill["topspin_rpm"][0], drill["topspin_rpm"][1]) * rpm_to_rads
    w2 = random.uniform(drill["sidespin_rpm"][0], drill["sidespin_rpm"][1]) * rpm_to_rads
    
    # 4. Map coordinates back to a Zone ID (Placeholder logic: simple 1-20 grid)
    # This ensures the feedback_queue tells the server WHICH LED was targeted.
    zone_id = int((target_X * 5) + (target_Y * 2)) 
    
    return target_X, target_Y, V, w1, w2, zone_id

def run_launcher_engine(command_queue, feedback_queue):
    """Renamed to match main.py expectations."""
    serial_port = '/dev/ttyUSB0'
    baud_rate = 921600
    TOTAL_SHOTS = 10
    
    try:
        esp32 = serial.Serial(serial_port, baud_rate, timeout=1)
        print(f"[Launcher Engine] UART connected at {baud_rate} baud.")
    except serial.SerialException:
        print("[Launcher Engine] WARNING: ESP32 not found. Simulation Mode active.")
        esp32 = None

    while True:
        # --- WAIT FOR DRILL START (Outside the shot loop) ---
        print("[Launcher Engine] Awaiting drill command from Server...")
        drill_id = command_queue.get() 
        
        if drill_id == 0: continue # Emergency Stop/Idle

        print(f"\n[Launcher Engine] COMMENCING DRILL {drill_id}: {TOTAL_SHOTS} shots.")

        for shot_number in range(1, TOTAL_SHOTS + 1):
            
            # --- EMERGENCY ABORT CHECK ---
            try:
                if command_queue.get_nowait() == 0:
                    print(f"[Launcher Engine] 🛑 ABORTED AT SHOT {shot_number}")
                    break
            except queue.Empty:
                pass

            target_X, target_Y, V, w1, w2, zone_id = generate_randomized_shot(drill_id)

            try:
                pitch, yaw = find_launch_parameters(target_X, target_Y, V, w1, w2)
                m1, m2, m3 = calculate_motor_rpms(V, w1, w2)
                
                # Format exactly for ESP32 C++ Parser
                uart_string = f"S:{pitch:.1f}:{yaw:.1f}:{int(m1)}:{int(m2)}:{int(m3)}:{zone_id}\n"
                
                if esp32:
                    esp32.write(uart_string.encode('ascii'))
                    esp32.flush()
                
                # Report to Feedback Queue for Server scoring
                feedback_queue.put({
                    "shot_number": shot_number, 
                    "active_zone": zone_id
                })
                print(f"[Launcher] Shot {shot_number} -> Zone {zone_id} | TX: {uart_string.strip()}")
                
            except TargetUnreachableError as e:
                print(f"[Launcher] MATH ERROR: {e}")
                feedback_queue.put({"shot_number": shot_number, "active_zone": -1, "status": "failed"})

            time.sleep(2.0) # Hardware reload delay
