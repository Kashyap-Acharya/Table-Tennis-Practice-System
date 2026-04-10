import numpy as np
from scipy.optimize import minimize

# ─── TEMPORARY STUBS (replace with real imports when teammates merge) ───────
def local_to_global(V, w1, w2, pitch, yaw):
    p = np.radians(pitch)
    y = np.radians(yaw)
    v_global = np.array([V*np.sin(y)*np.cos(p),
                         V*np.cos(y)*np.cos(p),
                         V*np.sin(p)])
    w_global = np.array([w1*np.cos(y), w1*np.sin(y), w2])
    return v_global, w_global

def generate_initial_guess(target_X, target_Y, V):
    dist = np.sqrt(target_X**2 + target_Y**2)
    g = 9.8015
    ratio = np.clip(g * dist / V**2, -1.0, 1.0)
    pitch = np.degrees(0.5 * np.arcsin(ratio))
    yaw   = np.degrees(np.arctan2(target_X, target_Y))
    return [pitch, yaw]

def predict_trajectory(v_global, w_global):
    # Stub: simple gravity-only drop, no drag/spin yet
    vx, vy, vz = v_global
    g = 9.8015
    if vz <= 0:
        return (0.0, 0.0)
    t_land = 2 * vz / g        # time to land (parabolic)
    landing_X = vx * t_land
    landing_Y = vy * t_land
    return (landing_X, landing_Y)
# ─────────────────────────────────────────────────────────────────────────────


def find_launch_parameters(target_X, target_Y, V, w1, w2):
    """
    Finds the Pitch and Yaw angles to land the ball at (target_X, target_Y).

    Args:
        target_X : float - target X coordinate on table (meters)
        target_Y : float - target Y coordinate on table (meters)
        V        : float - ball speed (m/s)
        w1       : float - topspin (rad/s)
        w2       : float - sidespin (rad/s)

    Returns:
        [Optimal_Pitch, Optimal_Yaw] in degrees

    Raises:
        Exception if optimizer cannot reach within 1 cm of target
    """

    def objective_function(guess_angles):
        pitch, yaw = guess_angles

        # Step 1: angles → global vectors (Task 2)
        v_global, w_global = local_to_global(V, w1, w2, pitch, yaw)

        # Step 2: global vectors → landing spot (Task 1)
        landing_X, landing_Y = predict_trajectory(v_global, w_global)

        # Step 3: distance error from target
        error = np.sqrt((landing_X - target_X)**2 + (landing_Y - target_Y)**2)
        return error

    # Get smart starting guess (Task 2)
    x0 = generate_initial_guess(target_X, target_Y, V)

    # Hardware physical limits
    bounds = [(0.0, 35.0), (-45.0, 45.0)]

    # Run optimizer
    result = minimize(
        fun=objective_function,
        x0=x0,
        method='L-BFGS-B',
        bounds=bounds,
        options={'ftol': 1e-9, 'gtol': 1e-7, 'maxiter': 500}
    )

    # Check tolerance
    if result.fun <= 0.01:
        optimal_pitch, optimal_yaw = result.x
        return [optimal_pitch, optimal_yaw]
    else:
        raise Exception(
            f"Optimizer failed. Best error: {result.fun*100:.2f} cm | "
            f"Pitch={result.x[0]:.2f}°, Yaw={result.x[1]:.2f}°"
        )