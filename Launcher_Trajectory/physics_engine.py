"""
physics_engine.py
=================
Issue #14 - Physics Simulation Engine

Standalone physics engine that calculates where a table tennis ball will land
based on its starting velocities and spins in global room coordinates.

Based on Chen (2010) aerodynamic model with Drag and Magnus forces.
"""

import numpy as np
from scipy.integrate import solve_ivp

# ──────────────────────────────────────────────
# Table Tennis Physical Constants (Chen 2010)
# ──────────────────────────────────────────────
BALL_MASS        = 0.0027        # kg  (2.7 g official ITF ball)
BALL_RADIUS      = 0.02          # m   (40 mm diameter)
BALL_AREA        = np.pi * BALL_RADIUS**2  # m²

RHO_AIR          = 1.293         # kg/m³  air density at sea level
G                = 9.81          # m/s²   gravitational acceleration

# Aerodynamic coefficients (Chen 2010 Table 1)
C_D              = 0.40          # drag coefficient
C_L              = 1.23          # lift (Magnus) coefficient  ← C_L * R / V ratio used below

# Derived constants
K_DRAG  = 0.5 * RHO_AIR * BALL_AREA * C_D / BALL_MASS   # 1/m  — drag per unit velocity²
K_MAGNUS = 0.5 * RHO_AIR * BALL_AREA * C_L * BALL_RADIUS / BALL_MASS  # Magnus scale factor

# Simulation settings
T_MAX   = 5.0    # s  — maximum integration time (ball must hit table before this)
Z_TABLE = 0.0    # m  — table surface z-coordinate (event target)
LAUNCHER_HEIGHT = 0.165  # m  — launcher nozzle height above table surface


def _aerodynamic_odes(t, state, w_global):
    """
    Right-hand side of the aerodynamic ODE system.

    State vector: [x, y, z, vx, vy, vz]

    Forces acting on the ball:
      1. Gravity:  F_g = -m*g  in z-direction
      2. Drag:     F_d = -k_drag * |v| * v
      3. Magnus:   F_m = k_magnus * (ω × v)

    Parameters
    ----------
    t        : float       — current time (unused, autonomous system)
    state    : array[6]    — [x, y, z, vx, vy, vz]
    w_global : array[3]    — [wx, wy, wz] spin vector in rad/s

    Returns
    -------
    dstate/dt : array[6]
    """
    _, _, _, vx, vy, vz = state
    v_vec = np.array([vx, vy, vz])
    w_vec = np.array(w_global, dtype=float)

    v_mag = np.linalg.norm(v_vec)

    # Drag force  (opposes velocity)
    if v_mag > 1e-9:
        a_drag = -K_DRAG * v_mag * v_vec
    else:
        a_drag = np.zeros(3)

    # Magnus force  (ω × v)
    a_magnus = K_MAGNUS * np.cross(w_vec, v_vec)

    # Gravity
    a_gravity = np.array([0.0, 0.0, -G])

    # Total acceleration
    a_total = a_gravity + a_drag + a_magnus

    return [vx, vy, vz, a_total[0], a_total[1], a_total[2]]


def _table_hit_event(t, state, w_global):
    """
    Event function for solve_ivp: triggers when ball z-coordinate reaches 0.0.
    The negative direction attribute makes it detect downward crossing.
    """
    return state[2] - Z_TABLE

_table_hit_event.terminal  = True   # stop integration on event
_table_hit_event.direction = -1     # only trigger on downward crossing (z decreasing)


def predict_trajectory(v_global, w_global):
    """
    Predict where a table tennis ball will land on the table.

    Parameters
    ----------
    v_global : array-like of float — [vx, vy, vz]  initial velocity  (m/s)
    w_global : array-like of float — [wx, wy, wz]  initial spin      (rad/s)

    Returns
    -------
    (landing_X, landing_Y) : tuple of float
        Coordinates (in metres) where the ball hits z = 0.
        Returns (None, None) if the ball never reaches z=0 within T_MAX seconds.

    Raises
    ------
    ValueError : if input vectors are not length-3
    """
    v_global = np.asarray(v_global, dtype=float)
    w_global = np.asarray(w_global, dtype=float)

    if v_global.shape != (3,) or w_global.shape != (3,):
        raise ValueError("v_global and w_global must both be 3-element arrays.")

    # Initial state: ball starts at launcher position (above the table)
    # The launcher is mounted at a height of ~0.30 m above the table surface
    x0, y0, z0 = 0.0, 0.0, LAUNCHER_HEIGHT
    initial_state = [x0, y0, z0, v_global[0], v_global[1], v_global[2]]

    sol = solve_ivp(
        fun=_aerodynamic_odes,
        t_span=(0.0, T_MAX),
        y0=initial_state,
        args=(w_global,),
        method='RK45',
        events=_table_hit_event,
        dense_output=False,
        rtol=1e-6,
        atol=1e-9,
    )

    # Check if the ball-hit-table event was triggered
    if sol.t_events[0].size > 0:
        # y_events[0] shape: (n_events, 6); take the first (and only) event
        landing_state = sol.y_events[0][0]
        landing_X = float(landing_state[0])
        landing_Y = float(landing_state[1])
        return (landing_X, landing_Y)
    else:
        # Ball never reached the table within T_MAX
        return (None, None)
