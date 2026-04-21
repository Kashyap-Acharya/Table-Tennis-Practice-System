import time
import serial
import random
import math
import queue
from optimizer import find_launch_parameters, TargetUnreachableError
from kinematics import calculate_motor_rpms

# ==========================================
# THE DRILL DICTIONARY (Updated)
# NEW COORDINATE SYSTEM:
# Origin (0,0,0) = Right end of the net (from the receiver's perspective).
# X-axis (Width): 0 to 1.525 m (Positive is moving leftwards across the net).
# Y-axis (Depth): 0 to 1.370 m (Positive is moving towards the receiver).
# Units in dict are cm and RPM. They will be converted to SI units in the code.
DRILL_DICT = {
    1: { "name": "Forehand Normal", "depth_cm": (60, 120), "width_cm": (15, 70),   "v_ms": (8, 11),  "sidespin_rpm": (0, 0),         "topspin_rpm": (300, 800) },
    2: { "name": "Backhand Normal", "depth_cm": (60, 120), "width_cm": (82, 137),  "v_ms": (7, 9),   "sidespin_rpm": (0, 0),         "topspin_rpm": (300, 800) },
    3: { "name": "Spin (Forehand)", "depth_cm": (30, 125), "width_cm": (15, 70),   "v_ms": (8, 12),  "sidespin_rpm": (-1500, 1500),  "topspin_rpm": (-500, 1000) }
}
# ==========================================

def generate_randomized_shot(drill_id):
    """Pulls ranges from the dictionary and strictly converts to SI units."""
    drill = DRILL_DICT[drill_id]
    
    # 1. Coordinate Conversion (cm to meters)
    target_X = random.uniform(drill["width_cm"][0], drill["width_cm"][1]) / 100.0
    target_Y = random.uniform(drill["depth_cm"][0], drill["depth_cm"][1]) / 100.0
    
    # 2. Velocity (Already in m/s)
    V = random.uniform(drill["v_ms"][0], drill["v_ms"][1])
    
    # 3. Spin Conversion (RPM to rad/s)
    # Formula: rad/s = RPM * (2 * pi / 60)
    rpm_to_rads = (2.0 * math.pi) / 60.0
    w1_rpm = random.uniform(drill["topspin_rpm"][0], drill["topspin_rpm"][1])
    w2_rpm = random.uniform(drill["sidespin_rpm"][0], drill["sidespin_rpm"][1])
    
    w1 = w1_rpm * rpm_to_rads
    w2 = w2_rpm * rpm_to_rads
    
    return target_X, target_Y, V, w1, w2


def main(command_queue, feedback_queue):
    # --- Configuration ---
    serial_port = '/dev/ttyUSB0'  # Typical ESP32 CP2102 port
    baud_rate = 921600            # Upgraded baud rate
    TOTAL_ITERATIONS = 10         # Variable defining number of balls per drill session
    
    print("[Launcher Engine] Booting sequence initialized...")
    
    # --- Initialize UART ---
    try:
        esp32 = serial.Serial(serial_port, baud_rate, timeout=1)
        print(f"[Launcher Engine] UART connected to ESP32 at {baud_rate} baud.")
    except serial.SerialException:
        print("[Launcher Engine] WARNING: ESP32 not found. Running in simulation mode.")
        esp32 = None

    print(f"[Launcher Engine] Commencing routine: {TOTAL_ITERATIONS} shots.")

    # --- Main Execution Loop ---
    for shot_number in range(1, TOTAL_ITERATIONS + 1):
        
        # 1. Block and wait for Drill ID from Server/CV
        try:
            # Using timeout prevents the thread from locking forever if the server crashes
            drill_id = command_queue.get(timeout=10) 
        except queue.Empty:
            print("[Launcher Engine] Timeout waiting for command. Aborting routine.")
            break

        print(f"\n--- Shot {shot_number}/{TOTAL_ITERATIONS} | Drill ID: {drill_id} ---")
        
        # 2. Generate target parameters in pure SI units
        target_X, target_Y, V, w1, w2 = generate_randomized_shot(drill_id)

        # 3. LED Zone Generation (Handled by your logic, using drill_id as placeholder)
        zone_id = drill_id 
        
        try:
            # 4. Feed the Physics/Math Engine
            pitch, yaw = find_launch_parameters(target_X, target_Y, V, w1, w2)
            m1, m2, m3 = calculate_motor_rpms(V, w1, w2)
            
            # 5. Format UART string exactly as expected by ESP32 C++ Code
            # String Format: S:Pitch:Yaw:M1:M2:M3:ZoneID\n
            uart_string = f"S:{pitch:.1f}:{yaw:.1f}:{int(m1)}:{int(m2)}:{int(m3)}:{zone_id}\n"
            
            # 6. Transmit to ESP32
            if esp32:
                esp32.write(uart_string.encode('ascii'))
                esp32.flush()  # Force OS to push buffer instantly over wire
                print(f"[Launcher] Transmitted: {uart_string.strip()}")
            else:
                print(f"[Launcher] SIMULATED TX: {uart_string.strip()}")
            
            # 7. Push Feedback to Server/CV Team
            feedback_queue.put({
                "shot_number": shot_number, 
                "active_zone": zone_id
            })
            print(f"[Launcher] Feedback queue updated -> Shot: {shot_number}, Zone: {zone_id}")
            
        except TargetUnreachableError as e:
            print(f"[Launcher] ERROR: {e}")
            # Push failed feedback so CV team knows not to expect a ball
            feedback_queue.put({"shot_number": shot_number, "active_zone": -1, "status": "failed"})

        # Wait before next shot to allow ESP32 to mechanically execute the sequence
        time.sleep(2.0)

    print("\n[Launcher Engine] Routine Complete.")

# Mock Execution for Standalone Testing
if __name__ == "__main__":
    dummy_cmd = queue.Queue()
    dummy_fb = queue.Queue()
    
    # Pre-fill queue with 10 random commands to test the loop
    for _ in range(10):
        dummy_cmd.put(random.choice(list(DRILL_DICT.keys())))
        
    main(dummy_cmd, dummy_fb)
