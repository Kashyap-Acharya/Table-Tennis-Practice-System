"""
physics_engine.py
=================
Physics Simulation Engine (SI Units: Meters, Seconds, Radians)

Solves the Chen (2010) aerodynamic differential equations to track 
table tennis ball trajectory. Uses the Receiver's Net Post as Origin (0,0,0).
"""

import numpy as np
from scipy.integrate import solve_ivp

# ──────────────────────────────────────────────
# 1. LAUNCHER PHYSICAL POSITION & COORDINATES
# ──────────────────────────────────────────────
# Origin (0,0,0) = Right end of the net (from receiver's perspective).
# +X Axis = Lateral left across the net (max width 1.525 m).
# +Y Axis = Forward depth towards the receiver (max depth 1.37 m).

# The launcher is mounted on the OPPOSITE side of the net:
# X: Centered on the table (1.525 / 2 = 0.7625 m)
# Y: 1.37m (table half-length) + 0.15m (15cm offset away from edge) = -1.52 m
# Z: 22cm above the table = 0.22 m
Launcher_y_offset = -0.15
LAUNCHER_X = 0.7625
LAUNCHER_Y = -1.37 + Launcher_y_offset
LAUNCHER_Z = 0.22

# ──────────────────────────────────────────────
# 2. AERODYNAMIC CONSTANTS (Chen 2010)
# ──────────────────────────────────────────────
BALL_MASS   = 0.0027      # kg
BALL_RADIUS = 0.021      # m

# P = P0 * exp(-g * h / (R * T))
# P_SEA_LEVEL = 101325 Standard pressure (Pa)
# R_SPECIFIC  = 287.058 Specific gas constant for dry air
# temp_k      = temp_c + 273.15 Convert to Kelvin
# pressure_alt = P_SEA_LEVEL * math.exp((-g_gandhinagar * altitude_m) / (R_SPECIFIC * temp_k))
# Using the Ideal Gas Law to find density (rho)
# rho = P / (R * T)
RHO_AIR     = 1.1426      # kg/m^3

# g = g_equator * (1 + k * sin^2(phi)) / sqrt(1 - e^2 * sin^2(phi))
G           = 9.7881        # m/s^2

# Derived Drag & Magnus Coefficients
C_D = 0.40  
C_L = 1.23  
AREA = np.pi * BALL_RADIUS**2

K_DRAG = 0.5 * RHO_AIR * AREA * C_D / BALL_MASS
K_MAGNUS = 0.5 * RHO_AIR * AREA * C_L * BALL_RADIUS / BALL_MASS

# ──────────────────────────────────────────────
# 3. CORE ODE SOLVER LOGIC
# ──────────────────────────────────────────────
def _aerodynamic_odes(t, state, w_global):
    """Calculates instantaneous X, Y, Z acceleration."""
    x, y, z, vx, vy, vz = state
    v_vector = np.array([vx, vy, vz])
    v_mag = np.linalg.norm(v_vector)
    
    if v_mag == 0:
        return [vx, vy, vz, 0, 0, -G]

    # Drag Force (Opposes velocity)
    a_drag = -K_DRAG * v_mag * v_vector

    # Magnus Force (Cross product of Spin and Velocity)
    a_magnus = K_MAGNUS * np.cross(w_global, v_vector)

    ax = a_drag[0] + a_magnus[0]
    ay = a_drag[1] + a_magnus[1]
    az = -G + a_drag[2] + a_magnus[2]

    return [vx, vy, vz, ax, ay, az]

def _table_hit_event(t, state, w_global):
    """Triggers when Z-coordinate hits 0.0 (The Table Surface)"""
    return state[2] 
_table_hit_event.terminal = True
_table_hit_event.direction = -1

def predict_trajectory(v_global, w_global):
    """
    Simulates flight from the Launcher's physical offsets.
    Returns the exact (X, Y) landing coordinate in the global room space.
    """
    initial_state = [LAUNCHER_X, LAUNCHER_Y, LAUNCHER_Z, v_global[0], v_global[1], v_global[2]]
    
    sol = solve_ivp(
        fun=_aerodynamic_odes,
        t_span=(0.0, 5.0), # Max 5 seconds of flight
        y0=initial_state,
        args=(np.asarray(w_global),),
        events=_table_hit_event,
        dense_output=False,
        rtol=1e-6,
        atol=1e-9
    )
    
    # If the solver correctly terminated by hitting the table:
    if sol.status == 1: 
        return sol.y[0][-1], sol.y[1][-1]
    
    return None, None
