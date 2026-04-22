"""
calibrate_homography.py  —  One-Time Lab Calibration Script
=============================================================
Run this ONCE on your physical table setup to generate the 3×3
homography matrix that maps camera pixels → LED screen mm coords.

Usage:
  python3 calibrate_homography.py

Instructions:
  1. Point the camera at the LED board.
  2. A window will open showing the camera feed.
  3. Click the 4 corners of the LED board IN ORDER:
        Click 1 → TOP-LEFT  corner of the board
        Click 2 → TOP-RIGHT corner of the board
        Click 3 → BOTTOM-RIGHT corner of the board
        Click 4 → BOTTOM-LEFT corner of the board
  4. The script will print the 3×3 matrix to the terminal.
  5. Copy it and paste into HomographyMapper.CALIBRATED_MATRIX in vision_core.py.

After calibration, also visually check the ROI constants by looking at
the printed pixel coordinates and updating these in vision_core.py:
  SCREEN_ROI_Y_TOP     ← minimum y pixel of the 4 corners
  SCREEN_ROI_Y_BOTTOM  ← maximum y pixel of the 4 corners
  SCREEN_ROI_X_LEFT    ← minimum x pixel of the 4 corners
  SCREEN_ROI_X_RIGHT   ← maximum x pixel of the 4 corners
"""

import cv2
import numpy as np
import subprocess
import time


# ── Physical screen dimensions (mm) ────────────────────────────────────────
# These are the REAL-WORLD coordinates of the 4 corners of the LED board.
# Origin = bottom-left of the screen.
#
#   (0, 200) ─────────────────── (1520, 200)   ← TOP edge
#       │                               │
#       │         LED SCREEN            │
#       │         152cm × 20cm          │
#       │                               │
#   (0,   0) ─────────────────── (1520,   0)   ← BOTTOM edge
#
# The click order must match this layout:
SCREEN_WIDTH_MM  = 1520   # 152 cm
SCREEN_HEIGHT_MM =  200   # 20  cm

# Order: TOP-LEFT, TOP-RIGHT, BOTTOM-RIGHT, BOTTOM-LEFT
REAL_WORLD_CORNERS = np.array([
    [0.0,          SCREEN_HEIGHT_MM],   # Top-Left
    [SCREEN_WIDTH_MM, SCREEN_HEIGHT_MM], # Top-Right
    [SCREEN_WIDTH_MM, 0.0            ],  # Bottom-Right
    [0.0,          0.0              ],   # Bottom-Left
], dtype=np.float32)

# ── Camera settings (match vision_core.py) ─────────────────────────────────
CAM_SRC    = 0
CAM_WIDTH  = 640
CAM_HEIGHT = 400
FPS        = 30   # Lower FPS for calibration — no need for 120

clicked_points = []


def mouse_callback(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(clicked_points) < 4:
            clicked_points.append((x, y))
            print(f"  Point {len(clicked_points)} clicked: px({x}, {y})")


def main():
    print("=" * 60)
    print("  Homography Calibration  —  Table Tennis CV")
    print("=" * 60)
    print(f"\nScreen: {SCREEN_WIDTH_MM}mm × {SCREEN_HEIGHT_MM}mm")
    print("\nClick the 4 corners of the LED board IN ORDER:")
    print("  1. TOP-LEFT")
    print("  2. TOP-RIGHT")
    print("  3. BOTTOM-RIGHT")
    print("  4. BOTTOM-LEFT")
    print("\nPress 'r' to reset clicks.  Press 'q' to quit without saving.\n")

    # Lock camera to manual mode (same as vision_core)
    subprocess.run(["v4l2-ctl", "-d", "/dev/video0", "-c", "auto_exposure=1"],
                   stderr=subprocess.DEVNULL)
    subprocess.run(["v4l2-ctl", "-d", "/dev/video0", "-c", "exposure_time_absolute=50"],
                   stderr=subprocess.DEVNULL)

    cap = cv2.VideoCapture(CAM_SRC, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC,       cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAM_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS,          FPS)
    time.sleep(1.0)

    cv2.namedWindow("Calibration")
    cv2.setMouseCallback("Calibration", mouse_callback)

    labels = ["1:Top-Left", "2:Top-Right", "3:Bottom-Right", "4:Bottom-Left"]
    colors = [(0,255,0), (0,165,255), (0,0,255), (255,0,0)]

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[Calibration] ERROR: Cannot read from camera.")
            break

        display = frame.copy()

        # Draw already-clicked points
        for i, (px, py) in enumerate(clicked_points):
            cv2.circle(display, (px, py), 8, colors[i], -1)
            cv2.putText(display, labels[i], (px + 10, py - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, colors[i], 2)

        # Draw connecting lines when all 4 points are in
        if len(clicked_points) == 4:
            pts = np.array(clicked_points, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(display, [pts], isClosed=True, color=(255, 255, 0), thickness=2)

        # Next click instruction
        if len(clicked_points) < 4:
            next_label = labels[len(clicked_points)]
            cv2.putText(display, f"Click: {next_label}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        else:
            cv2.putText(display, "4 points set! Press ENTER to compute.",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        cv2.imshow("Calibration", display)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('r'):
            clicked_points.clear()
            print("[Calibration] Points reset.")

        elif key == 13 and len(clicked_points) == 4:   # ENTER
            src_pts = np.array(clicked_points, dtype=np.float32)
            dst_pts = REAL_WORLD_CORNERS

            matrix, status = cv2.findHomography(src_pts, dst_pts)

            if matrix is None:
                print("[Calibration] ERROR: findHomography failed. "
                      "Are your 4 points forming a valid quadrilateral?")
            else:
                print("\n" + "=" * 60)
                print("  ✅  CALIBRATION SUCCESSFUL")
                print("=" * 60)
                print("\nPaste this into HomographyMapper.CALIBRATED_MATRIX")
                print("in vision_core.py:\n")
                print("CALIBRATED_MATRIX = np.array([")
                for row in matrix:
                    print(f"    [{row[0]:>14.6e},  {row[1]:>14.6e},  {row[2]:>14.6e}],")
                print("], dtype=np.float64)")

                print("\n── Screen ROI pixel bounds (update in vision_core.py) ──")
                px_vals = [p[0] for p in clicked_points]
                py_vals = [p[1] for p in clicked_points]
                print(f"SCREEN_ROI_X_LEFT   = {max(0, min(px_vals) - 10)}")
                print(f"SCREEN_ROI_X_RIGHT  = {min(CAM_WIDTH, max(px_vals) + 10)}")
                print(f"SCREEN_ROI_Y_TOP    = {max(0, min(py_vals) - 10)}")
                print(f"SCREEN_ROI_Y_BOTTOM = {min(CAM_HEIGHT, max(py_vals) + 10)}")

                # Verification: map the 4 source corners back and compare
                print("\n── Verification (should match 0/1520/0/200 mm) ──")
                for i, (px_pt, py_pt) in enumerate(clicked_points):
                    src = np.array([px_pt, py_pt, 1.0], dtype=np.float64)
                    res = matrix @ src
                    w   = res[2]
                    y_mm = res[0] / w
                    z_mm = res[1] / w
                    print(f"  Corner {i+1} px({px_pt},{py_pt}) → "
                          f"({y_mm:.1f}mm, {z_mm:.1f}mm)  "
                          f"[expected: {REAL_WORLD_CORNERS[i]}]")

                break

        elif key == ord('q'):
            print("[Calibration] Quit without saving.")
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()