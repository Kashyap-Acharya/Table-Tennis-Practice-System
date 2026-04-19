"""
optimizer.py
============
Issue #16 — Core Targeting Loop (Optimizer)

Links the CV user input to the physics engine via a Scipy L-BFGS-B optimizer.
Iteratively guesses Pitch and Yaw to minimize the Euclidean distance between
the simulated landing spot and the actual requested target.
"""

import numpy as np
import scipy.optimize as opt

from kinematics import local_to_global, generate_initial_guess
from physics_engine import predict_trajectory

# ──────────────────────────────────────────────
# Hardware limits  (from Issue #16)
# ──────────────────────────────────────────────
PITCH_MIN =  0.0    # degrees
PITCH_MAX = 35.0    # degrees

YAW_MIN   = -45.0   # degrees
YAW_MAX   =  45.0   # degrees

BOUNDS = [(PITCH_MIN, PITCH_MAX), (YAW_MIN, YAW_MAX)]

# Tolerance: 1 cm in metres
ERROR_TOLERANCE = 0.01   # m


class TargetUnreachableError(Exception):
    """Raised when the optimizer fails to find a solution within tolerance."""
    pass


def objective_function(guess_angles, target_X, target_Y, V, w1, w2):
    """
    Cost function for the optimizer.

    Calls local_to_global -> predict_trajectory and returns the Euclidean
    distance between the simulated landing spot and [target_X, target_Y].

    Parameters
    ----------
    guess_angles : array-like [pitch, yaw]  — current optimizer guess (degrees)
    target_X     : float   — desired landing x-coordinate  (m)
    target_Y     : float   — desired landing y-coordinate  (m)
    V            : float   — ball launch speed              (m/s)
    w1           : float   — topspin  (rad/s)
    w2           : float   — sidespin (rad/s)

    Returns
    -------
    distance : float  — Euclidean error (m) between simulated and target landing.
               Returns 1e6 (large penalty) if the ball never reaches the table.
    """
    pitch, yaw = float(guess_angles[0]), float(guess_angles[1])

    # Step 1: Convert local launcher frame -> global room frame
    v_global, w_global = local_to_global(V, w1, w2, pitch, yaw)

    # Step 2: Simulate trajectory
    landing = predict_trajectory(v_global, w_global)

    # Step 3: Compute distance error
    if landing[0] is None:
        return 1e6   # ball never hit the table

    landing_X, landing_Y = landing
    error = np.sqrt((landing_X - target_X)**2 + (landing_Y - target_Y)**2)
    return error


def find_launch_parameters(target_X, target_Y, V, w1, w2):
    """
    Find the optimal pitch and yaw angles to land the ball at (target_X, target_Y).

    Algorithm
    ---------
    1. Generate initial guess from vacuum ballistics (kinematics.generate_initial_guess).
    2. Multi-start L-BFGS-B with hardware bounds for fast convergence.
    3. Polish pass with tight tolerances (gradient-based L-BFGS-B).
    4. Check final error <= 1 cm tolerance.
    5. Return optimal angles or raise TargetUnreachableError.

    Parameters
    ----------
    target_X : float   — desired landing x-coordinate  (m)
    target_Y : float   — desired landing y-coordinate  (m)
    V        : float   — ball launch speed              (m/s)
    w1       : float   — topspin  (rad/s)
    w2       : float   — sidespin (rad/s)

    Returns
    -------
    (Optimal_Pitch, Optimal_Yaw) : tuple of float   — angles in degrees

    Raises
    ------
    TargetUnreachableError : if optimizer cannot converge within 1 cm tolerance.
                             This usually means the target is outside the
                             physically reachable range for the given V and
                             hardware angle bounds.
    """
    args = (target_X, target_Y, V, w1, w2)

    # ── Step 1: Generate initial guess from vacuum ballistics ──────────────
    initial_pitch, initial_yaw = generate_initial_guess(target_X, target_Y, V)
    initial_pitch = float(np.clip(initial_pitch, PITCH_MIN, PITCH_MAX))
    initial_yaw   = float(np.clip(initial_yaw,   YAW_MIN,   YAW_MAX))

    # ── Step 2: Multi-start L-BFGS-B ──────────────────────────────────────
    # Uses coarser finite-difference step (eps=0.5 deg) suited to the
    # noisy ODE cost landscape.
    best_result = None

    start_candidates = [
        [initial_pitch, initial_yaw],   # ballistics guess (primary)
        [15.0,          initial_yaw],   # mid-range pitch
        [25.0,          initial_yaw],   # high pitch
        [5.0,           initial_yaw],   # low pitch
        [initial_pitch, 0.0],           # zero-yaw variant
    ]

    for x0_raw in start_candidates:
        x0 = np.clip(np.array(x0_raw, dtype=float),
                     [PITCH_MIN, YAW_MIN],
                     [PITCH_MAX, YAW_MAX])

        res = opt.minimize(
            fun=objective_function,
            x0=x0,
            args=args,
            method='L-BFGS-B',
            bounds=BOUNDS,
            options={
                'eps':     0.5,
                'ftol':    1e-15,
                'gtol':    1e-10,
                'maxiter': 2000,
                'maxfun':  10000,
            },
        )

        if best_result is None or res.fun < best_result.fun:
            best_result = res

        if best_result.fun <= ERROR_TOLERANCE:
            break

    # ── Step 3: Polish pass ───────────────────────────────────────────────
    # If the multi-start L-BFGS-B didn't hit the 1cm tolerance, we run one 
    # final, highly-precise L-BFGS-B pass starting from the best known point.
    if best_result.fun > ERROR_TOLERANCE:
        
        # Native L-BFGS-B handles bounds cleanly without penalty hacks
        lbfgsb_res = opt.minimize(
            fun=objective_function,
            x0=best_result.x,
            args=args,
            method='L-BFGS-B',
            bounds=BOUNDS,
            options={
                'ftol': 1e-8,
                'gtol': 1e-8,
                'maxiter': 1000
            }
        )

        # If the polish pass improved the error, save it as the best result
        if lbfgsb_res.fun < best_result.fun:
            best_result = lbfgsb_res

    # ── Step 4: Check tolerance ────────────────────────────────────────────
    final_error = best_result.fun
    if final_error > ERROR_TOLERANCE:
        raise TargetUnreachableError(
            f"Optimizer failed to hit target ({target_X:.3f}, {target_Y:.3f}) "
            f"within 1 cm tolerance. Best error: {final_error*100:.2f} cm. "
            f"The target is likely outside the physically reachable range for "
            f"V={V:.1f} m/s with pitch in [{PITCH_MIN}, {PITCH_MAX}] deg "
            f"and yaw in [{YAW_MIN}, {YAW_MAX}] deg."
        )

    # ── Step 5: Return optimal angles ─────────────────────────────────────
    optimal_pitch, optimal_yaw = best_result.x
    return (float(optimal_pitch), float(optimal_yaw))
