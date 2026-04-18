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
"""

import numpy as np
import math

# ──────────────────────────────────────────────
# Physical constants
# ──────────────────────────────────────────────
G            = 9.787   # m/s²   gravitational acceleration
WHEEL_RADIUS = 0.025    # m      launcher wheel radius (30 mm — adjust to hardware)
RPM_PER_RAD  = 60.0 / (2.0 * math.pi)   # conversion factor

# Wheel offsets for 3-wheeled omni configuration (degrees from forward axis +X,
# measured in the YZ plane — the plane perpendicular to ball travel).
# Physical layout: Straight "Y" configuration (viewed from front)
# 0° is Right, 90° is Up, 180° is Left
#   Wheel 1: 270° — Bottom
#   Wheel 2: 150° — Top-Left
#   Wheel 3:  30° — Top-Right
WHEEL_ANGLES_DEG = [270,150,30]   # 120° apart, first wheel at 90°
WHEEL_ANGLES_RAD = [math.radians(a) for a in WHEEL_ANGLES_DEG]


# ══════════════════════════════════════════════
# 1. Vector Translation
# ══════════════════════════════════════════════

def local_to_global(v, w1, w2, pitch, yaw):
    """
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
    pitch_rad = math.radians(pitch)
    yaw_rad   = math.radians(yaw)

    # ── Rotation matrix around Z axis (yaw) ──────────────────────────────
    # Rotates the horizontal plane
    R_yaw = np.array([
        [ math.cos(yaw_rad), -math.sin(yaw_rad), 0.0],
        [ math.sin(yaw_rad),  math.cos(yaw_rad), 0.0],
        [               0.0,                0.0, 1.0],
    ])

    # ── Rotation matrix around Y axis (pitch) ────────────────────────────
    # Positive pitch tilts the launcher upward (+Z direction)
    # Standard right-hand rule around Y axis: positive angle lifts +X toward +Z
    R_pitch = np.array([
        [ math.cos(pitch_rad), 0.0, -math.sin(pitch_rad)],
        [                 0.0, 1.0,                  0.0],
        [ math.sin(pitch_rad), 0.0,  math.cos(pitch_rad)],
    ])

    # Combined rotation: first pitch, then yaw
    R = R_yaw @ R_pitch

    # ── Local velocity vector: ball fired along +X axis ──────────────────
    v_local = np.array([v, 0.0, 0.0])

    # ── Local spin vector: topspin about local Y, sidespin about local Z ─
    # w1 (topspin)  → spin around launcher's Y axis
    # w2 (sidespin) → spin around launcher's Z axis
    w_local = np.array([0.0, w1, w2])

    # ── Rotate into global frame ─────────────────────────────────────────
    v_global = R @ v_local
    w_global = R @ w_local

    return v_global, w_global


# ══════════════════════════════════════════════
# 2. Initial Guesser
# ══════════════════════════════════════════════

def generate_initial_guess(target_X, target_Y, V):
    """
    Generate a mathematically sound initial (pitch, yaw) guess using
    standard vacuum ballistics.

    Vacuum ballistics formula:
        θ = 0.5 * arcsin( g * Distance / V² )

    The optimal launch angle for maximum distance is 45°. If the target
    is too far for the given V, we clamp to 45°.

    Parameters
    ----------
    target_X : float   — target x-coordinate in global room frame  (m)
    target_Y : float   — target y-coordinate in global room frame  (m)
    V        : float   — ball launch speed                          (m/s)

    Returns
    -------
    (initial_pitch, initial_yaw) : tuple of float   — both in degrees
    """
    # Horizontal distance to target
    distance = math.sqrt(target_X**2 + target_Y**2)

    # Yaw: angle to aim at the target in the horizontal plane
    # atan2(Y, X) gives the angle from +X axis
    initial_yaw = math.degrees(math.atan2(target_Y, target_X))

    # Pitch: vacuum ballistics
    # θ = 0.5 * arcsin(g * distance / V²)
    if V < 1e-6:
        initial_pitch = 45.0   # degenerate case
    else:
        sin_arg = G * distance / (V ** 2)
        if sin_arg >= 1.0:
            # Target is beyond maximum range — use 45° (maximum range angle)
            initial_pitch = 45.0
        else:
            initial_pitch = math.degrees(0.5 * math.asin(sin_arg))

    return (initial_pitch, initial_yaw)


# ══════════════════════════════════════════════
# 3. Motor Translation
# ══════════════════════════════════════════════

def calculate_motor_rpms(V, w1, w2):
    """
    Map forward velocity and spin commands to motor RPMs for a
    3-wheeled omni-wheel launcher configuration.

    Physical layout: Straight "Y" configuration
      Wheel 1: Bottom    (270°)
      Wheel 2: Top-Left  (150°)
      Wheel 3: Top-Right (30°)

    Each wheel's tangential velocity contribution is derived from the rigid body 
    kinematics cross product (V_contact = V_center + w x r):
    
      v_wheel_i = V + R_ball * (w1 * sin(θ_i) + w2 * cos(θ_i))

    This gracefully handles the sine/cosine projections exactly as derived:
      - Bottom wheel (270°): pure V - w1*R
      - Top wheels: V + w1*R*sin(30°) ± w2*R*cos(30°)

    Parameters
    ----------
    V  : float   — desired ball forward speed (m/s)
    w1 : float   — topspin  (rad/s, about local +Y axis)
    w2 : float   — sidespin (rad/s, about local +Z axis)

    Returns
    -------
    (M1_RPM, M2_RPM, M3_RPM) : tuple of int   — motor RPMs (rounded to nearest integer)
    """
    BALL_RADIUS = 0.02  # m — table tennis ball radius

    rpms = []
    for angle in WHEEL_ANGLES_RAD:
        # Calculate the rotational contribution using exact rigid body kinematics
        # sin() maps to the Z-axis (topspin effect), cos() maps to the Y-axis (sidespin effect)
        v_spin_effect = BALL_RADIUS * (w1 * math.sin(angle) + w2 * math.cos(angle))
        
        # Total tangential velocity at this wheel
        v_tangential = V + v_spin_effect

        # Convert surface velocity to Motor RPM
        rpm = (v_tangential / WHEEL_RADIUS) * RPM_PER_RAD
        rpms.append(int(round(rpm)))

    return (rpms[0], rpms[1], rpms[2])
