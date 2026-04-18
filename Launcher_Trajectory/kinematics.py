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
G            = 9.81    # m/s²   gravitational acceleration
WHEEL_RADIUS = 0.025    # m      launcher wheel radius (30 mm — adjust to hardware)
RPM_PER_RAD  = 60.0 / (2.0 * math.pi)   # conversion factor

# Wheel offsets for 3-wheeled omni configuration (degrees from forward axis)
WHEEL_ANGLES_DEG = [90.0, 210.0, 330.0]   # 120° apart, first wheel at 90°
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

    Wheel layout (offset by 120°):
      Wheel 1: 90°   (left)
      Wheel 2: 210°  (lower-right)
      Wheel 3: 330°  (upper-right)

    Each wheel's tangential velocity contribution:
      v_wheel_i = V * cos(θ_i) + w1 * R_ball (topspin contribution)
                  + w2 * R_ball * sin(θ_i)    (sidespin contribution)

    Then RPM = (tangential_velocity / wheel_radius) * (60 / 2π)

    Parameters
    ----------
    V  : float   — desired ball forward speed (m/s)
    w1 : float   — topspin  (rad/s)
    w2 : float   — sidespin (rad/s)

    Returns
    -------
    (M1_RPM, M2_RPM, M3_RPM) : tuple of int   — motor RPMs (rounded to nearest integer)
    """
    BALL_RADIUS = 0.02  # m — ball radius for spin-to-surface-velocity conversion

    rpms = []
    for angle in WHEEL_ANGLES_RAD:
        # Forward velocity component at this wheel's orientation
        v_forward_component = V * math.cos(angle)

        # Topspin adds a surface velocity equal to w1 * ball_radius
        # (same direction for all wheels — uniformly adds backspin or topspin)
        v_topspin = w1 * BALL_RADIUS

        # Sidespin adds a differential velocity based on wheel's lateral position
        v_sidespin = w2 * BALL_RADIUS * math.sin(angle)

        # Total tangential velocity at this wheel
        v_tangential = v_forward_component + v_topspin + v_sidespin

        # Convert to RPM
        rpm = (v_tangential / WHEEL_RADIUS) * RPM_PER_RAD
        rpms.append(int(round(rpm)))

    return (rpms[0], rpms[1], rpms[2])
