"""
vision_core.py  —  Table Tennis CV Core (Production Ready - Corrected)
===========================================================
Project:  Smart Table Tennis Training System
Hardware: Arducam OV9281 (M12 lens, Global Shutter), Raspberry Pi 5
Screen:   152cm × 20cm LED board (1520mm × 200mm)
"""

import cv2
import numpy as np
import struct
import time
import subprocess
import threading
from multiprocessing import shared_memory
from collections import deque


# ============================================================
# SECTION 1 — CONFIGURATION
# ============================================================
FPS        = 120
DT         = 1.0 / FPS
CAM_WIDTH  = 640  
CAM_HEIGHT = 400  
CAM_SRC    = 0    

EXPOSURE_ABS = 50   
GAIN         = 15   

LOWER_WHITE_THRESH = 220 
MOTION_THRESH      = 25 
USE_MOTION_FUSION  = True 
MIN_AREA           = 10  
MAX_AREA           = 1200 
MAX_ENCLOSING_R    = 40  

# Adaptive Tracking Constraints
GATE_RADIUS_PX   = 50       
MAX_FRAMES_LOST  = 30       

# Physical Screen Dimensions
SCREEN_WIDTH_MM  = 1520  
SCREEN_HEIGHT_MM = 200   
SCREEN_OUTLIER_TOLERANCE_MM = 50

# Screen ROI (Update after mounting camera)
SCREEN_ROI_Y_TOP    = 0    
SCREEN_ROI_Y_BOTTOM = 400  
SCREEN_ROI_X_LEFT   = 0    
SCREEN_ROI_X_RIGHT  = 640  

DEBUG_DISPLAY = True 

# ============================================================
# SECTION 2 — HARDWARE LOCK
# ============================================================
def lock_arducam_hardware():
    print("[Hardware] Locking Arducam OV9281 settings...")
    cmds = [
        ["v4l2-ctl", "-d", "/dev/video0", "-c", "auto_exposure=1"],
        ["v4l2-ctl", "-d", "/dev/video0", "-c", f"exposure_time_absolute={EXPOSURE_ABS}"],
        ["v4l2-ctl", "-d", "/dev/video0", "-c", f"gain={GAIN}"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, stderr=subprocess.DEVNULL)
    print(f"[Hardware] Locked. Exposure={EXPOSURE_ABS}, Gain={GAIN}")

# ============================================================
# SECTION 3 — THREADED CAMERA (With Race-Condition Locks)
# ============================================================
class ThreadedCamera:
    def __init__(self, src=0):
        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        # FIX: Reverted to GREY for the monochrome OV9281 sensor
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'GREY'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, FPS)

        time.sleep(1.0)
        self.ret, self.frame = self.cap.read()
        self.running = True
        
        self.lock = threading.Lock() # Kills frame-tearing
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.ret, self.frame = ret, frame

    def read(self):
        # FIX: Correctly structured lock to prevent NoneType crashes
        with self.lock:
            if self.frame is not None:
                return self.ret, self.frame.copy()
            return False, None

    def release(self):
        self.running = False
        self.thread.join(timeout=2.0)
        self.cap.release()

# ============================================================
# SECTION 4 — ADAPTIVE KALMAN FILTER
# ============================================================
class ConstantVelocityKalmanFilter:
    def __init__(self, dt: float):
        self.x = np.zeros((4, 1), dtype=np.float64)
        self.F = np.array([[1, 0, dt, 0],
                           [0, 1, 0, dt],
                           [0, 0, 1, 0],
                           [0, 0, 0, 1]], dtype=np.float64)
        self.H = np.array([[1, 0, 0, 0],
                           [0, 1, 0, 0]], dtype=np.float64)
                           
        self.R = np.eye(2, dtype=np.float64) * 2.0 
        self.P = np.eye(4, dtype=np.float64) * 100.0
        self.I = np.eye(4, dtype=np.float64)

    def set_initial_state(self, x: float, y: float):
        self.x = np.array([[x], [y], [0.0], [0.0]], dtype=np.float64)
        self.P = np.eye(4, dtype=np.float64) * 100.0 

    def predict(self) -> np.ndarray:
        # ADAPTIVE Q: Filter loosens when ball is fast, tightens when slow
        speed = np.hypot(self.x[2, 0], self.x[3, 0])
        self.Q = np.eye(4, dtype=np.float64) * (0.05 + (0.01 * speed))
        
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.x

    def update(self, z: np.ndarray) -> np.ndarray:
        y_innov = z - (self.H @ self.x)
        S       = self.H @ self.P @ self.H.T + self.R
        K       = self.P @ self.H.T @ np.linalg.inv(S)
        self.x  = self.x + (K @ y_innov)
        self.P  = (self.I - K @ self.H) @ self.P
        return self.x

# ============================================================
# SECTION 5 — FAST VISION
# ============================================================
class FastVision:
    def __init__(self):
        self.prev_gray  = None
        self.kernel     = np.ones((3, 3), np.uint8)

    def process(self, frame, pred_x=None, pred_y=None, use_roi=False):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame

        if self.prev_gray is None:
            self.prev_gray = gray
            return None, np.zeros_like(gray)

        _, bright_mask = cv2.threshold(gray, LOWER_WHITE_THRESH, 255, cv2.THRESH_BINARY)
        diff = cv2.absdiff(self.prev_gray, gray)
        _, motion_mask = cv2.threshold(diff, MOTION_THRESH, 255, cv2.THRESH_BINARY)
        self.prev_gray = gray

        if USE_MOTION_FUSION:
            dilated_motion = cv2.dilate(motion_mask, self.kernel, iterations=1)
            mask = cv2.bitwise_and(bright_mask, dilated_motion)
        else:
            mask = bright_mask

        if use_roi:
            roi_mask = np.zeros_like(mask)
            roi_mask[SCREEN_ROI_Y_TOP:SCREEN_ROI_Y_BOTTOM, SCREEN_ROI_X_LEFT:SCREEN_ROI_X_RIGHT] = 255
            mask = cv2.bitwise_and(mask, roi_mask)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best, best_score = None, -float('inf')

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if not (MIN_AREA < area < MAX_AREA): continue

            (cx, cy), radius = cv2.minEnclosingCircle(cnt)
            if radius <= 0 or radius > MAX_ENCLOSING_R: continue

            # FIX: Removed the buggy MAX_PHYSICS_JUMP trap. We now rely on the Kalman Filter gate.
            score = area - (2.0 * np.hypot(cx - pred_x, cy - pred_y)) if pred_x is not None else area

            if score > best_score:
                best_score = score
                best = (int(cx), int(cy), radius)

        return best, mask

# ============================================================
# SECTION 6 — WINDOWED PHYSICS DETECTOR
# ============================================================
class PhysicsHitDetector:
    def __init__(self):
        self.history  = deque(maxlen=15)
        self.state    = "AWAITING_TABLE_BOUNCE"
        self.cooldown = 0

    def reset(self):
        self.history.clear()
        self.state    = "AWAITING_TABLE_BOUNCE"
        self.cooldown = 0 

    def check_impacts(self, x: float, y: float, vx: float, vy: float):
        self.history.append((x, y, vx, vy))

        if self.cooldown > 0:
            self.cooldown -= 1
            return None

        # REQUIRES 4 FRAMES: prevents single-frame noise glitches
        if len(self.history) < 4:
            return None

        # Grab trailing 4-frame window
        recent_vy = [h[3] for h in list(self.history)[-4:]]
        recent_vx = [h[2] for h in list(self.history)[-4:]]

        # ── Stage 1: Table Bounce ──────────────────────────────
        if self.state == "AWAITING_TABLE_BOUNCE":
            # Early frame was falling, late frame is rising
            if recent_vy[0] > 3.0 and recent_vy[-1] < -3.0:
                self.state    = "AWAITING_SCREEN_HIT"
                self.cooldown = 10
                # Extract coordinates from the moment it flipped
                return ("TABLE", self.history[-3][0], self.history[-3][1])

        # ── Stage 2: Screen Impact ─────────────────────────────
        elif self.state == "AWAITING_SCREEN_HIT":
            # Case A: Lob shot (Y-axis flips)
            y_inversion = (recent_vy[0] < -2.0 and recent_vy[-1] > 2.0)
            
            # Case B: Laser Smash (X-axis flips)
            x_inversion = (abs(recent_vx[0]) > 3.0 and 
                           abs(recent_vx[-1]) > 3.0 and 
                           (recent_vx[0] * recent_vx[-1] < 0))

            if y_inversion or x_inversion:
                self.state    = "AWAITING_TABLE_BOUNCE"
                self.cooldown = 15
                return ("SCREEN", self.history[-3][0], self.history[-3][1])

        return None

# ============================================================
# SECTION 7 — HOMOGRAPHY & IPC DUMB PIPE
# ============================================================
class HomographyMapper:
    # ⚠️ Replace with output from your 4-point click script or calibration.json!
    CALIBRATED_MATRIX = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    def __init__(self):
        self.matrix = self.CALIBRATED_MATRIX.copy()
        if np.allclose(self.matrix, np.eye(3)):
            print("[Homography] ⚠️ WARNING: Using identity matrix. Calibration needed.")

    def pixel_to_mm(self, px: float, py: float):
        src = np.array([px, py, 1.0], dtype=np.float64)
        res = self.matrix @ src
        w   = res[2]

        if abs(w) < 1e-9: return None

        raw_y, raw_z = res[0] / w, res[1] / w

        # Reject if math wildly throws coordinates off the physical board
        if (raw_y < -SCREEN_OUTLIER_TOLERANCE_MM or raw_y > SCREEN_WIDTH_MM  + SCREEN_OUTLIER_TOLERANCE_MM or
            raw_z < -SCREEN_OUTLIER_TOLERANCE_MM or raw_z > SCREEN_HEIGHT_MM + SCREEN_OUTLIER_TOLERANCE_MM):
            return None

        # Clamp to physical bounds
        return float(np.clip(raw_y, 0.0, SCREEN_WIDTH_MM)), float(np.clip(raw_z, 0.0, SCREEN_HEIGHT_MM))

class IPCManager:
    STRUCT_FMT  = '<BBHHB3x22x'
    STRUCT_SIZE = struct.calcsize(STRUCT_FMT)
    SHM_NAME    = "tt_cv_bridge"

    def __init__(self):
        try:
            self.shm = shared_memory.SharedMemory(name=self.SHM_NAME, create=True, size=self.STRUCT_SIZE)
        except FileExistsError:
            self.shm = shared_memory.SharedMemory(name=self.SHM_NAME)
        self._zero()

    def _zero(self):
        self.shm.buf[:self.STRUCT_SIZE] = struct.pack(self.STRUCT_FMT, 0, 0, 0, 0, 0)

    def is_flag_clear(self):
        return struct.unpack(self.STRUCT_FMT, self.shm.buf[:self.STRUCT_SIZE])[0] == 0

    def write_hit(self, y_mm: float, z_mm: float):
        if not self.is_flag_clear():
            return False

        safe_y = max(0, min(65535, int(round(y_mm))))
        safe_z = max(0, min(65535, int(round(z_mm))))

        # FIX: Changed the second byte from 0 to 1 to match your original IPC format (1, 1, y, z, 0)
        self.shm.buf[:self.STRUCT_SIZE] = struct.pack(self.STRUCT_FMT, 1, 1, safe_y, safe_z, 0)
        return True

    def cleanup(self):
        try:
            self._zero()
            self.shm.close()
            self.shm.unlink()
        except Exception:
            pass

# ============================================================
# SECTION 8 — MAIN LOOP
# ============================================================
def main():
    print("=" * 60)
    print("  Table Tennis CV Core  —  Production Environment Ready")
    print("=" * 60)

    lock_arducam_hardware()
    cam      = ThreadedCamera(src=CAM_SRC)
    vision   = FastVision()
    kf       = ConstantVelocityKalmanFilter(DT)
    detector = PhysicsHitDetector()
    mapper   = HomographyMapper()
    ipc      = IPCManager()

    kf_initialized = False
    frames_lost    = 0
    pred_x, pred_y = None, None

    try:
        while True:
            ret, frame = cam.read()
            if not ret or frame is None:
                continue

            if kf_initialized:
                pred = kf.predict()
                pred_x, pred_y = float(pred[0, 0]), float(pred[1, 0])

            use_roi = (detector.state == "AWAITING_SCREEN_HIT")
            obs, mask = vision.process(frame, pred_x, pred_y, use_roi=use_roi)

            if obs is not None:
                cx, cy, r = obs

                if not kf_initialized:
                    kf.set_initial_state(cx, cy)
                    kf_initialized = True
                    pred_x, pred_y = float(cx), float(cy)
                    frames_lost = 0

                else:
                    dist = np.hypot(cx - pred_x, cy - pred_y)
                    if dist < GATE_RADIUS_PX:
                        state_vec = kf.update(np.array([[cx], [cy]], dtype=np.float64))
                        frames_lost = 0

                        vx, vy = float(state_vec[2, 0]), float(state_vec[3, 0])
                        impact = detector.check_impacts(float(state_vec[0, 0]), float(state_vec[1, 0]), vx, vy)

                        if impact is not None:
                            event, imp_px, imp_py = impact

                            if event == "TABLE":
                                print(f"[Main] 🏓 TABLE BOUNCE at px({imp_px:.0f}, {imp_py:.0f})")

                            elif event == "SCREEN":
                                mm_result = mapper.pixel_to_mm(imp_px, imp_py)
                                if mm_result is not None:
                                    y_mm, z_mm = mm_result
                                    written = ipc.write_hit(y_mm, z_mm)
                                    status = "IPC written" if written else "IPC SKIPPED (flag not clear)"
                                    print(f"[Main] 🎯 SCREEN IMPACT: Y={y_mm:.1f}mm Z={z_mm:.1f}mm → {status}")
                    else:
                        frames_lost += 1
            else:
                if kf_initialized:
                    frames_lost += 1

            if frames_lost > MAX_FRAMES_LOST:
                kf_initialized = False
                frames_lost = 0
                pred_x, pred_y = None, None
                detector.reset() 

            if DEBUG_DISPLAY:
                d_frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if len(frame.shape) == 2 else frame.copy()
                if obs is not None:
                    cv2.circle(d_frame, (obs[0], obs[1]), int(obs[2])+4, (0, 0, 255), 2)
                elif kf_initialized and pred_x is not None:
                    cv2.circle(d_frame, (int(pred_x), int(pred_y)), 6, (255, 80, 0), -1)
                
                cv2.putText(d_frame, f"State: {detector.state}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.imshow("CV Core", d_frame)
                
                if cv2.waitKey(1) & 0xFF == 27:
                    break

    except KeyboardInterrupt:
        print("\n[Main] Shutdown signal received.")

    finally:
        cam.release()
        ipc.cleanup()
        cv2.destroyAllWindows()
        print("[Main] Engine offline.")

if __name__ == "__main__":
    main()