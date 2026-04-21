"""
kinematics.py
=============
Kinematics Issue — Math Helper Library

Mathematical helper functions that translate data between user requests,
the global physics room, and the physical motors.

Functions
---------
local_to_global(v, w1, w2, pitch, yaw)
    Rotate local velocity & spin vectors into global room coordinates.

generate_initial_guess(target_X, target_Y, V)
    Use vacuum ballistics to produce a good starting (pitch, yaw) for the optimizer.

calculate_motor_rpms(V, w1, w2)
    Map forward velocity + spins to RPMs for the 3-wheeled omni launcher.

Rotate local velocity and spin vectors into global room coordinates
using the rotation matrices defined in WOE_Launcher_DataFlow.pdf.

The launcher's local frame:
+X  — forward (direction the ball is fired)
+Y  — lateral left
+Z  — up

The rotation is applied as:  R_global = R_yaw @ R_pitch
Then:  v_global = R_global @ [v, 0, 0]
w_global = R_global @ [0, w1, w2]
where w1 = topspin (about local Y), w2 = sidespin (about local Z)

Parameters
----------
v     : float   — ball speed magnitude            (m/s)
w1    : float   — topspin  (angular velocity)     (rad/s, positive = topspin)
w2    : float   — sidespin (angular velocity)     (rad/s, positive = right-spin)
pitch : float   — launcher pitch angle            (degrees, +ve = upward)
yaw   : float   — launcher yaw angle              (degrees, +ve = left of centre)

Returns
-------
v_global : np.ndarray [vx, vy, vz]   — velocity in global frame   (m/s)
w_global : np.ndarray [wx, wy, wz]   — spin    in global frame   (rad/s)
"""

"""
kinematics.py
=============
Kinematics Math Engine

Translates ballistics and velocities between the physical motors, 
the launcher's local barrel frame, and the global room coordinates.
"""

import math
from physics_engine import LAUNCHER_X, LAUNCHER_Y, LAUNCHER_Z, G, BALL_RADIUS

# Your Flywheel Dimensions
WHEEL_RADIUS = 0.025  # 5cm diameter = 2.5cm radius
RPM_PER_RAD = 60.0 / (2.0 * math.pi)

def local_to_global(V, w1, w2, pitch_deg, yaw_deg):
    """
    Rotates the Launcher's firing vectors into the room's Global axes.
    +Y is Forward, +X is Launcher's Right (Receiver's Left), +Z is Up.
    """
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)

    # 1. Velocity Rotation
    vx = V * math.sin(yaw) * math.cos(pitch)
    vy = V * math.cos(yaw) * math.cos(pitch)
    vz = V * math.sin(pitch)

    # 2. Spin Rotation
    # w1 > 0 (Topspin): Pushes ball Down (-Z). Spin axis must be -X.
    # w2 > 0 (Right Sidespin): Pushes ball Right (+X). Spin axis must be -Z.
    wx = -w1 * math.cos(yaw)
    wy = w1 * math.sin(yaw)
    wz = -w2 

    return [vx, vy, vz], [wx, wy, wz]

def generate_initial_guess(target_X, target_Y, V):
    """
    Provides a smart mathematical starting point for the Scipy Optimizer.
    Distance is calculated dynamically using the launcher's physical offsets.
    """
    # Distance from the physical launcher to the global target coordinate
    dx = target_X - LAUNCHER_X
    dy = target_Y - LAUNCHER_Y
    distance = math.sqrt(dx**2 + dy**2)

    # Basic vacuum ballistics formula for starting Pitch
    val = (G * distance) / (V**2)
    val = max(-1.0, min(1.0, val)) # Clamp to prevent math domain errors on edge cases
    initial_pitch = math.degrees(0.5 * math.asin(val))

    # Basic trigonometry for starting Yaw 
    initial_yaw = math.degrees(math.atan2(dx, dy))

    return [initial_pitch, initial_yaw]

def calculate_motor_rpms(V, w1, w2):
    """
    Translates required Velocity and Spin to 3-wheel tangential speeds.
    Configuration: M1(30° Top-Right), M2(150° Top-Left), M3(270° Bottom-Center)
    """
    # All 3 wheels push forward equally to achieve V
    # Topspin (w1) slows down M3, speeds up M1 & M2
    # Sidespin (w2) splits laterally between M1 and M2
    
    # Cos/Sin constants pre-calculated for the exact angles
    v_m1 = V + (BALL_RADIUS * w1 * 0.5) - (BALL_RADIUS * w2 * 0.866)
    v_m2 = V + (BALL_RADIUS * w1 * 0.5) - (BALL_RADIUS * w2 * -0.866)
    v_m3 = V + (BALL_RADIUS * w1 * -1.0) - (BALL_RADIUS * w2 * 0.0)

    rpm_m1 = int((v_m1 / WHEEL_RADIUS) * RPM_PER_RAD)
    rpm_m2 = int((v_m2 / WHEEL_RADIUS) * RPM_PER_RAD)
    rpm_m3 = int((v_m3 / WHEEL_RADIUS) * RPM_PER_RAD)

    return rpm_m1, rpm_m2, rpm_m3
