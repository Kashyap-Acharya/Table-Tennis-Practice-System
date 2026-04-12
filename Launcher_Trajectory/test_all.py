"""
test_all.py
===========
Comprehensive test suite for:
  - physics_engine.py    (Issue #14)
  - kinematics.py        (Kinematics Issue)
  - optimizer.py         (Issue #16)

Reachable-range reference (pitch in [0,35], hardware bounds):
  V=5 m/s  → 1.18 m … 2.30 m
  V=6 m/s  → 1.40 m … 2.98 m
  V=7 m/s  → 1.62 m … 3.69 m
  V=8 m/s  → 1.83 m … 4.39 m
  V=10 m/s → 2.25 m … 5.76 m

All optimizer test targets are chosen WITHIN the reachable range for their V.

Run with:
    python -m pytest test_all.py -v
or:
    python test_all.py
"""

import math
import numpy as np
import pytest
import sys, os

sys.path.insert(0, os.path.dirname(__file__))

from physics_engine import predict_trajectory, LAUNCHER_HEIGHT
from kinematics import local_to_global, generate_initial_guess, calculate_motor_rpms
from optimizer import find_launch_parameters, TargetUnreachableError


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Physics Engine Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPhysicsEngine:

    def test_ball_lands_with_pitch(self):
        """Ball fired with slight upward pitch from launcher height must land ahead."""
        v_g, w_g = local_to_global(8.0, 0.0, 0.0, 10.0, 0.0)
        lx, ly = predict_trajectory(v_g, w_g)
        assert lx is not None, "Ball should land (z reaches 0)"
        assert lx > 0.5, "Ball should land well ahead of launcher"

    def test_ball_lands_with_upward_component(self):
        """Higher pitch should produce longer range (up to ~35 deg optimum)."""
        v_g_low,  w = local_to_global(8.0, 0.0, 0.0, 5.0,  0.0), [0,0,0]
        v_g_high, _ = local_to_global(8.0, 0.0, 0.0, 20.0, 0.0), [0,0,0]
        lx_low,  _ = predict_trajectory(v_g_low[0],  [0,0,0])
        lx_high, _ = predict_trajectory(v_g_high[0], [0,0,0])
        assert lx_high > lx_low, "Higher pitch should travel further (sub-optimal range)"

    def test_sidespin_deflects_lateral(self):
        """Positive sidespin (wz) should produce non-zero lateral landing."""
        v_g, w_g = local_to_global(8.0, 0.0, 50.0, 10.0, 0.0)
        lx, ly = predict_trajectory(v_g, w_g)
        assert lx is not None
        assert abs(ly) > 0.01, "Sidespin should produce lateral offset"

    def test_returns_none_for_no_landing(self):
        """Ball fired straight up should not reach z=0 within T_MAX."""
        # Straight up at high speed — won't come back down in 5 s
        lx, ly = predict_trajectory([0.0, 0.0, 500.0], [0.0, 0.0, 0.0])
        # vz=500 exceeds what drag can stop in T_MAX=5s — ball truly never returns
        assert lx is None and ly is None, "Extreme upward shot should not land in T_MAX=5s"

    def test_invalid_input_raises(self):
        """Wrong-shape inputs must raise ValueError."""
        with pytest.raises(ValueError):
            predict_trajectory([1.0, 2.0], [0.0, 0.0, 0.0])
        with pytest.raises(ValueError):
            predict_trajectory([1.0, 0.0, 0.5], [0.0, 0.0])

    def test_landing_is_reproducible(self):
        """Deterministic ODE must always return identical results."""
        v = [8.0, 0.2, 1.0]
        w = [0.0, 30.0, -10.0]
        assert predict_trajectory(v, w) == predict_trajectory(v, w)

    def test_topspin_shortens_range(self):
        """Topspin (Magnus force downward) reduces landing distance."""
        v_g_base, _ = local_to_global(8.0, 0.0,   0.0, 15.0, 0.0)
        v_g_top,  _ = local_to_global(8.0, 80.0,  0.0, 15.0, 0.0)
        lx_base, _ = predict_trajectory(v_g_base, [0.0, 0.0, 0.0])
        # For topspin, w_global must be rotated too
        v_g_top2, w_g_top2 = local_to_global(8.0, 80.0, 0.0, 15.0, 0.0)
        lx_top, _ = predict_trajectory(v_g_top2, w_g_top2)
        assert lx_base is not None and lx_top is not None
        assert lx_top < lx_base, "Topspin should shorten range"

    def test_launcher_height_is_positive(self):
        """Sanity: LAUNCHER_HEIGHT constant must be positive."""
        assert LAUNCHER_HEIGHT > 0.0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Kinematics Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLocalToGlobal:

    def test_zero_pitch_zero_yaw_velocity(self):
        """pitch=0, yaw=0 → velocity stays along +X."""
        v_g, _ = local_to_global(10.0, 0.0, 0.0, 0.0, 0.0)
        np.testing.assert_allclose(v_g, [10.0, 0.0, 0.0], atol=1e-10)

    def test_pure_yaw_rotates_in_horizontal_plane(self):
        """yaw=90° rotates velocity from +X to +Y."""
        v_g, _ = local_to_global(5.0, 0.0, 0.0, 0.0, 90.0)
        np.testing.assert_allclose(v_g, [0.0, 5.0, 0.0], atol=1e-10)

    def test_pure_pitch_tilts_upward(self):
        """pitch=90° points velocity straight up (+Z)."""
        v_g, _ = local_to_global(5.0, 0.0, 0.0, 90.0, 0.0)
        np.testing.assert_allclose(v_g, [0.0, 0.0, 5.0], atol=1e-10)

    def test_speed_magnitude_preserved(self):
        """Rotation preserves velocity magnitude."""
        v_g, _ = local_to_global(7.3, 0.0, 0.0, 23.0, -17.0)
        np.testing.assert_allclose(np.linalg.norm(v_g), 7.3, atol=1e-10)

    def test_spin_magnitude_preserved(self):
        """Rotation preserves spin magnitude."""
        _, w_g = local_to_global(5.0, 30.0, -10.0, 15.0, 20.0)
        expected_mag = math.sqrt(30.0**2 + 10.0**2)
        np.testing.assert_allclose(np.linalg.norm(w_g), expected_mag, atol=1e-10)

    def test_zero_spin_returns_zero_spin_global(self):
        """Zero local spin → zero global spin."""
        _, w_g = local_to_global(5.0, 0.0, 0.0, 30.0, 45.0)
        np.testing.assert_allclose(w_g, [0.0, 0.0, 0.0], atol=1e-10)

    def test_returns_numpy_arrays(self):
        """Return types are numpy arrays of shape (3,)."""
        v_g, w_g = local_to_global(5.0, 0.0, 0.0, 10.0, 5.0)
        assert isinstance(v_g, np.ndarray) and v_g.shape == (3,)
        assert isinstance(w_g, np.ndarray) and w_g.shape == (3,)

    def test_negative_yaw_goes_right(self):
        """yaw=-90° rotates velocity from +X to -Y (right)."""
        v_g, _ = local_to_global(5.0, 0.0, 0.0, 0.0, -90.0)
        np.testing.assert_allclose(v_g, [0.0, -5.0, 0.0], atol=1e-10)


class TestGenerateInitialGuess:

    def test_direct_shot_along_x(self):
        """Target straight ahead (Y=0) → yaw ≈ 0°."""
        pitch, yaw = generate_initial_guess(2.0, 0.0, 10.0)
        assert abs(yaw) < 1e-6
        assert 0.0 < pitch <= 45.0

    def test_lateral_target_gives_nonzero_yaw(self):
        """Diagonal target → non-zero yaw."""
        _, yaw = generate_initial_guess(1.0, 1.0, 10.0)
        assert abs(yaw) > 1.0

    def test_far_target_clamps_to_45(self):
        """Unreachable target → pitch clamped to 45°."""
        pitch, _ = generate_initial_guess(1000.0, 0.0, 1.0)
        assert pitch == 45.0

    def test_close_target_gives_low_pitch(self):
        """Closer target needs lower pitch than farther target at same V."""
        pitch_close, _ = generate_initial_guess(0.5, 0.0, 8.0)
        pitch_far,   _ = generate_initial_guess(3.0, 0.0, 8.0)
        assert pitch_close < pitch_far

    def test_returns_tuple_of_floats(self):
        result = generate_initial_guess(2.0, 1.0, 6.0)
        assert isinstance(result, tuple) and len(result) == 2
        assert all(isinstance(x, float) for x in result)

    def test_negative_target_y_gives_negative_yaw(self):
        """Target to the right → negative yaw."""
        _, yaw = generate_initial_guess(2.0, -1.0, 10.0)
        assert yaw < 0.0


class TestCalculateMotorRPMs:

    def test_returns_tuple_of_three_ints(self):
        result = calculate_motor_rpms(5.0, 0.0, 0.0)
        assert isinstance(result, tuple) and len(result) == 3
        assert all(isinstance(x, int) for x in result)

    def test_zero_inputs_give_zero_rpms(self):
        assert calculate_motor_rpms(0.0, 0.0, 0.0) == (0, 0, 0)

    def test_topspin_only_gives_equal_rpms(self):
        """Pure topspin (V=0) contributes uniformly to all wheels."""
        m1, m2, m3 = calculate_motor_rpms(0.0, 100.0, 0.0)
        assert m1 == m2 == m3

    def test_sidespin_differentiates_wheels(self):
        """Sidespin creates differential RPMs across wheels."""
        m1, m2, m3 = calculate_motor_rpms(0.0, 0.0, 100.0)
        assert len(set([m1, m2, m3])) > 1

    def test_higher_velocity_doubles_rpms(self):
        """Doubling V (no spin) should double average |RPM|."""
        m1a, m2a, m3a = calculate_motor_rpms(5.0,  0.0, 0.0)
        m1b, m2b, m3b = calculate_motor_rpms(10.0, 0.0, 0.0)
        avg_a = (abs(m1a) + abs(m2a) + abs(m3a)) / 3
        avg_b = (abs(m1b) + abs(m2b) + abs(m3b)) / 3
        if avg_a > 0:
            np.testing.assert_allclose(avg_b / avg_a, 2.0, rtol=0.05)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Optimizer Tests
# ══════════════════════════════════════════════════════════════════════════════
# NOTE: All targets are within the reachable range for their V:
#   V=8 → range 1.83–4.39 m   V=7 → range 1.62–3.69 m

class TestOptimizer:

    def test_basic_target_reachable(self):
        """Mid-range target at V=8 should produce angles within hardware bounds."""
        pitch, yaw = find_launch_parameters(2.5, 0.0, 8.0, 0.0, 0.0)
        assert PITCH_MIN <= pitch <= PITCH_MAX
        assert YAW_MIN   <= yaw   <= YAW_MAX

    def test_solution_actually_lands_near_target(self):
        """Returned angles should physically land within 2 cm of target."""
        target_X, target_Y, V = 2.5, 0.5, 8.0
        pitch, yaw = find_launch_parameters(target_X, target_Y, V, 0.0, 0.0)
        v_g, w_g = local_to_global(V, 0.0, 0.0, pitch, yaw)
        lx, ly = predict_trajectory(v_g, w_g)
        assert lx is not None
        error = math.sqrt((lx - target_X)**2 + (ly - target_Y)**2)
        assert error <= 0.02, f"Landing error {error*100:.2f} cm > 2 cm"

    def test_returns_floats(self):
        """Return value must be a 2-tuple of floats."""
        result = find_launch_parameters(2.0, 0.0, 7.0, 0.0, 0.0)
        assert isinstance(result, tuple) and len(result) == 2
        assert all(isinstance(x, float) for x in result)

    def test_unreachable_target_raises(self):
        """Impossibly far target must raise TargetUnreachableError."""
        with pytest.raises(TargetUnreachableError):
            find_launch_parameters(500.0, 0.0, 5.0, 0.0, 0.0)

    def test_lateral_target(self):
        """Lateral target (non-zero Y) should produce non-zero yaw."""
        pitch, yaw = find_launch_parameters(2.5, 0.8, 8.0, 0.0, 0.0)
        assert abs(yaw) > 0.5, "Lateral target must require non-zero yaw"
        assert PITCH_MIN <= pitch <= PITCH_MAX
        assert YAW_MIN   <= yaw   <= YAW_MAX

    def test_target_too_close_raises(self):
        """Target inside minimum range for given V must raise."""
        # V=8, min range ~1.83 m; target at 0.5 m is unreachable
        with pytest.raises(TargetUnreachableError):
            find_launch_parameters(0.5, 0.0, 8.0, 0.0, 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: Integration Tests (full pipeline)
# ══════════════════════════════════════════════════════════════════════════════

class TestFullPipeline:

    def test_end_to_end_pipeline(self):
        """Optimizer → kinematics → physics → motor RPMs, full chain."""
        target_X, target_Y = 2.5, 0.0
        V, w1, w2 = 8.0, 20.0, 0.0

        pitch, yaw       = find_launch_parameters(target_X, target_Y, V, w1, w2)
        v_global, w_global = local_to_global(V, w1, w2, pitch, yaw)
        lx, ly           = predict_trajectory(v_global, w_global)
        M1, M2, M3       = calculate_motor_rpms(V, w1, w2)

        assert lx is not None
        error = math.sqrt((lx - target_X)**2 + (ly - target_Y)**2)
        assert error <= 0.02, f"E2E error {error*100:.2f} cm > 2 cm"
        assert all(isinstance(m, int) for m in [M1, M2, M3])

        print(f"\n[E2E] Target:  ({target_X}, {target_Y})")
        print(f"[E2E] Pitch:   {pitch:.4f}°   Yaw: {yaw:.4f}°")
        print(f"[E2E] v_global:{np.round(v_global, 3)}")
        print(f"[E2E] w_global:{np.round(w_global, 3)}")
        print(f"[E2E] Landing: ({lx:.4f}, {ly:.4f})  error={error*100:.2f} cm")
        print(f"[E2E] RPMs:    M1={M1}  M2={M2}  M3={M3}")

    def test_pipeline_with_sidespin(self):
        """Full pipeline with sidespin: Magnus effect accounted for."""
        target_X, target_Y = 2.5, 0.2
        V, w1, w2 = 8.0, 0.0, 30.0

        pitch, yaw = find_launch_parameters(target_X, target_Y, V, w1, w2)
        v_global, w_global = local_to_global(V, w1, w2, pitch, yaw)
        lx, ly = predict_trajectory(v_global, w_global)

        assert lx is not None
        error = math.sqrt((lx - target_X)**2 + (ly - target_Y)**2)
        assert error <= 0.02, f"Sidespin pipeline error {error*100:.2f} cm"


# ══════════════════════════════════════════════════════════════════════════════
# IMPORT for bounds (needed in tests)
# ══════════════════════════════════════════════════════════════════════════════
from optimizer import PITCH_MIN, PITCH_MAX, YAW_MIN, YAW_MAX


# ══════════════════════════════════════════════════════════════════════════════
# Standalone runner (no pytest required)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 65)
    print("T4-Trainer Test Suite — running without pytest")
    print("=" * 65)

    passed = 0
    failed = 0
    failures = []

    def run(name, fn):
        global passed, failed
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}")
            print(f"        {type(e).__name__}: {e}")
            failed += 1
            failures.append((name, e))

    # ── Physics Engine ──────────────────────────────────────────────────────
    pe = TestPhysicsEngine()
    print("\n[1] Physics Engine")
    run("ball_lands_with_pitch",         pe.test_ball_lands_with_pitch)
    run("higher_pitch_longer_range",     pe.test_ball_lands_with_upward_component)
    run("sidespin_deflects_lateral",     pe.test_sidespin_deflects_lateral)
    run("straight_up_returns_none",      pe.test_returns_none_for_no_landing)
    run("invalid_input_raises",          pe.test_invalid_input_raises)
    run("deterministic_ode",             pe.test_landing_is_reproducible)
    run("topspin_shortens_range",        pe.test_topspin_shortens_range)
    run("launcher_height_positive",      pe.test_launcher_height_is_positive)

    # ── local_to_global ────────────────────────────────────────────────────
    l2g = TestLocalToGlobal()
    print("\n[2] local_to_global")
    run("zero_pitch_zero_yaw",           l2g.test_zero_pitch_zero_yaw_velocity)
    run("pure_yaw_horizontal",           l2g.test_pure_yaw_rotates_in_horizontal_plane)
    run("pure_pitch_upward",             l2g.test_pure_pitch_tilts_upward)
    run("speed_magnitude_preserved",     l2g.test_speed_magnitude_preserved)
    run("spin_magnitude_preserved",      l2g.test_spin_magnitude_preserved)
    run("zero_spin_stays_zero",          l2g.test_zero_spin_returns_zero_spin_global)
    run("returns_numpy_arrays",          l2g.test_returns_numpy_arrays)
    run("negative_yaw_goes_right",       l2g.test_negative_yaw_goes_right)

    # ── generate_initial_guess ─────────────────────────────────────────────
    ig = TestGenerateInitialGuess()
    print("\n[3] generate_initial_guess")
    run("direct_shot_zero_yaw",          ig.test_direct_shot_along_x)
    run("lateral_nonzero_yaw",           ig.test_lateral_target_gives_nonzero_yaw)
    run("far_target_clamps_45",          ig.test_far_target_clamps_to_45)
    run("close_target_low_pitch",        ig.test_close_target_gives_low_pitch)
    run("returns_tuple_floats",          ig.test_returns_tuple_of_floats)
    run("negative_y_negative_yaw",       ig.test_negative_target_y_gives_negative_yaw)

    # ── calculate_motor_rpms ───────────────────────────────────────────────
    mr = TestCalculateMotorRPMs()
    print("\n[4] calculate_motor_rpms")
    run("returns_3_ints",                mr.test_returns_tuple_of_three_ints)
    run("zero_inputs_zero_rpms",         mr.test_zero_inputs_give_zero_rpms)
    run("topspin_uniform_rpms",          mr.test_topspin_only_gives_equal_rpms)
    run("sidespin_differentiates",       mr.test_sidespin_differentiates_wheels)
    run("double_v_double_rpm",           mr.test_higher_velocity_doubles_rpms)

    # ── Optimizer ──────────────────────────────────────────────────────────
    ot = TestOptimizer()
    print("\n[5] Optimizer")
    run("basic_target_reachable",        ot.test_basic_target_reachable)
    run("lands_near_target",             ot.test_solution_actually_lands_near_target)
    run("returns_floats",                ot.test_returns_floats)
    run("unreachable_far_raises",        ot.test_unreachable_target_raises)
    run("lateral_target_nonzero_yaw",    ot.test_lateral_target)
    run("target_too_close_raises",       ot.test_target_too_close_raises)

    # ── Integration ────────────────────────────────────────────────────────
    intg = TestFullPipeline()
    print("\n[6] Full Pipeline Integration")
    run("end_to_end_no_spin",            intg.test_end_to_end_pipeline)
    run("end_to_end_sidespin",           intg.test_pipeline_with_sidespin)

    # ── Summary ────────────────────────────────────────────────────────────
    total = passed + failed
    print(f"\n{'=' * 65}")
    print(f"  {passed}/{total} tests passed   |   {failed} failed")
    if failures:
        print("\n  Failed tests:")
        for name, err in failures:
            print(f"    - {name}: {err}")
    print("=" * 65)
    sys.exit(0 if failed == 0 else 1)
