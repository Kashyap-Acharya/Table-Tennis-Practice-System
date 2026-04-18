# T4 Trainer — Launcher Trajectory

## System Overview

The T4 Trainer system has three repos that work in sequence:

```
CV Repo              Launcher Trajectory (this repo)       Firmware Repo
──────────           ───────────────────────────────       ─────────────
Detects target  →   Computes pitch, yaw, motor RPMs   →   Drives motors
position            using physics simulation                and servos
```

This repo takes as input:
- `(target_X, target_Y)` — where the ball should land on the table (metres)
- `V` — desired ball launch speed (m/s)
- `w1, w2` — desired topspin and sidespin (rad/s)

And produces:
- `(pitch, yaw)` — optimal servo angles (degrees)
- `(M1_RPM, M2_RPM, M3_RPM)` — motor speeds for the 3-wheel launcher

---

## Architecture

The repo is split into three layers, each with a single responsibility:

```
┌─────────────────────────────────────────────────────┐
│                    optimizer.py                      │
│           "Find the best pitch and yaw"              │
│  Iteratively guesses angles, simulates each shot,    │
│  and minimises landing error using L-BFGS-B +        │
│  Nelder-Mead until error < 1 cm                      │
└────────────┬──────────────────────┬─────────────────┘
             │                      │
             ▼                      ▼
┌────────────────────┐   ┌──────────────────────────┐
│   kinematics.py    │   │     physics_engine.py     │
│  "Do the geometry" │   │   "Simulate the flight"   │
│                    │   │                           │
│ - Rotate velocity  │   │ - Gravity, drag, Magnus   │
│   and spin from    │   │   force ODE system        │
│   local → global  │   │ - RK45 numerical solver   │
│   coordinates      │   │ - Returns landing (X, Y)  │
│ - Compute motor    │   │                           │
│   RPMs             │   │                           │
└────────────────────┘   └──────────────────────────┘
```

---

## Module Reference

### kinematics.py

**Purpose:** Pure mathematics — coordinate rotation and motor mapping. No simulation.

#### Constants

| Constant | Value | Description |
|---|---|---|
| `WHEEL_RADIUS` | `0.03` m | Radius of each launcher wheel |
| `WHEEL_ANGLES_DEG` | `[90, 210, 330]` | Angular position of each wheel (degrees from forward axis) |

#### Functions

**`generate_initial_guess(target_X, target_Y, V)`**  
Uses vacuum ballistics (`θ = ½ · arcsin(g·d / V²)`) to produce a starting (pitch, yaw) estimate for the optimizer. This is a fast approximation — it ignores air resistance and spin. The optimizer refines it from here.

**`local_to_global(v, w1, w2, pitch, yaw)`**  
Rotates the ball's velocity and spin from the launcher's local frame (+X = forward) into the global room frame, using a combined pitch-then-yaw rotation matrix. Called by the optimizer on every iteration.

**`calculate_motor_rpms(V, w1, w2)`**  
Maps desired speed and spin to RPMs for each of the 3 omni-wheels. Each wheel contributes differently depending on its angular position:
- Forward speed → scaled by `cos(wheel_angle)`
- Topspin → flat surface velocity (`w1 × ball_radius`)
- Sidespin → scaled by `sin(wheel_angle)`

---

### physics_engine.py

**Purpose:** Simulates the full aerodynamic flight of the ball from launch to table impact.

#### Constants

| Constant | Value | Description |
|---|---|---|
| `LAUNCHER_HEIGHT` | `0.30` m | Height of launcher nozzle above the table surface |
| `Z_TABLE` | `0.0` m | Z-coordinate of the table surface |
| `BALL_MASS` | `0.0027` kg | Official ITF ball mass |
| `BALL_RADIUS` | `0.02` m | Ball radius (40 mm diameter) |
| `C_D` | `0.40` | Drag coefficient (Chen 2010) |
| `C_L` | `1.23` | Magnus lift coefficient (Chen 2010) |
| `T_MAX` | `5.0` s | Maximum simulation time before giving up |

#### Functions

**`predict_trajectory(v_global, w_global)`**  
Main entry point. Takes initial velocity `[vx, vy, vz]` and spin `[wx, wy, wz]` in global coordinates. Runs a RK45 ODE solver and returns `(landing_X, landing_Y)` where the ball hits `z = 0`, or `(None, None)` if the ball never reaches the table.

**`_aerodynamic_odes(t, state, w_global)`**  
Internal ODE right-hand side. Computes acceleration at each time step from three forces:
- **Gravity:** `[0, 0, -g]`
- **Drag:** `-k_drag × |v| × v` (opposes velocity)
- **Magnus:** `k_magnus × (ω × v)` (curves the ball based on spin)

---

### optimizer.py

**Purpose:** Finds the exact pitch and yaw that lands the ball at the requested target, within 1 cm tolerance.

#### Constants

| Constant | Value | Description |
|---|---|---|
| `PITCH_MIN` | `0.0°` | Minimum servo pitch angle |
| `PITCH_MAX` | `35.0°` | Maximum servo pitch angle |
| `YAW_MIN` | `-45.0°` | Maximum left yaw angle |
| `YAW_MAX` | `45.0°` | Maximum right yaw angle |
| `ERROR_TOLERANCE` | `0.01` m | Acceptable landing error (1 cm) |

#### Functions

**`find_launch_parameters(target_X, target_Y, V, w1, w2)`**  
Main public function. Runs the full optimization loop and returns `(optimal_pitch, optimal_yaw)`. Raises `TargetUnreachableError` if no solution is found within tolerance.

**Optimization strategy:**
1. Get starting angles from `kinematics.generate_initial_guess`
2. Run L-BFGS-B from 5 different starting points (to avoid local minima)
3. If still not converged, run a Nelder-Mead polish pass
4. Check final error against 1 cm tolerance
5. Return angles or raise `TargetUnreachableError`

**`objective_function(guess_angles, ...)`**  
Called hundreds of times during optimization. For a given (pitch, yaw) guess:
1. Calls `kinematics.local_to_global` → gets global velocity/spin vectors
2. Calls `physics_engine.predict_trajectory` → simulates flight
3. Returns Euclidean distance between simulated and target landing position

---

## Hardware Parameters — Where to Update

When hardware changes, here is exactly what to update and where:

| Hardware change | File | Variable |
|---|---|---|
| Launcher wheel size changed | `kinematics.py` | `WHEEL_RADIUS` |
| Launcher mounted higher or lower | `physics_engine.py` | `LAUNCHER_HEIGHT` |
| Launcher moved sideways on the table | `physics_engine.py` | `x0, y0` inside `predict_trajectory` |
| Servo pitch range changed | `optimizer.py` | `PITCH_MIN`, `PITCH_MAX` |
| Servo yaw range changed | `optimizer.py` | `YAW_MIN`, `YAW_MAX` |
| Wheel layout changed (different angles) | `kinematics.py` | `WHEEL_ANGLES_DEG` |
| Different ball used (size/mass) | `physics_engine.py` | `BALL_RADIUS`, `BALL_MASS` |
| Table height changed | `physics_engine.py` | `Z_TABLE` |

---

## Data Flow

```
Input: (target_X, target_Y, V, w1, w2)
                    │
                    ▼
         optimizer.find_launch_parameters
                    │
         ┌──────────┴──────────┐
         │                     │
         ▼                     │
kinematics.generate_           │  (initial guess only)
  initial_guess                │
         │                     │
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────────────────────┐
         │     Optimization loop (~100s calls)  │
         │                                      │
         │  kinematics.local_to_global          │
         │    (pitch, yaw) → (v_global,         │
         │                    w_global)          │
         │            │                         │
         │            ▼                         │
         │  physics_engine.predict_trajectory   │
         │    (v_global, w_global)              │
         │    → (landing_X, landing_Y)          │
         │            │                         │
         │            ▼                         │
         │  error = dist(landing, target)       │
         │  repeat until error < 1 cm           │
         └──────────────────────────────────────┘
                    │
                    ▼
         (optimal_pitch, optimal_yaw)
                    │
                    ▼
         kinematics.calculate_motor_rpms
         (V, w1, w2) → (M1_RPM, M2_RPM, M3_RPM)
                    │
                    ▼
         Output to Firmware Repo → Physical Motors
```

---

## Dependencies

```
numpy
scipy
```

Install with:
```bash
pip install numpy scipy
```

---

*Aerodynamic model based on: Chen et al. (2010) — Table Tennis Ball Aerodynamics*
