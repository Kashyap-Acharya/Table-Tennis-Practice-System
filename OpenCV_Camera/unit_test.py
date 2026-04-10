import matplotlib.pyplot as plt

def run_kalman_unit_test():
    # --- 1. Setup Parameters ---
    dt = 1.0        # 1 second per frame
    std_acc = 0.5   # Process noise (how much the ball might deviate from constant velocity)
    std_meas = 3.0  # Measurement noise (how jittery the sensor is)
    num_frames = 50

    kf = ConstantVelocityKalmanFilter(dt, std_acc, std_meas)

    # --- 2. Generate Ground Truth (Straight Line) ---
    true_vx, true_vy = 2.0, 1.5
    true_x = [i * true_vx for i in range(num_frames)]
    true_y = [i * true_vy for i in range(num_frames)]

    # --- 3. Add Jitter (Simulated Sensor Noise) ---
    measured_x = true_x + np.random.normal(0, std_meas, num_frames)
    measured_y = true_y + np.random.normal(0, std_meas, num_frames)

    # --- 4. Run the Kalman Filter ---
    filtered_x = []
    filtered_y = []

    for i in range(num_frames):
        # Format measurement as a 2x1 column vector
        z = np.array([[measured_x[i]], 
                      [measured_y[i]]])
        
        # Step 1: Predict where the ball is
        kf.predict()
        
        # Step 2: Update with the noisy measurement
        state = kf.update(z)
        
        # Extract the smoothed position (x is index 0, y is index 1)
        filtered_x.append(state[0, 0])
        filtered_y.append(state[1, 0])

    # --- 5. Quantitative Test (Calculate Mean Squared Error) ---
    mse_raw = np.mean((np.array(true_x) - np.array(measured_x))**2 + 
                      (np.array(true_y) - np.array(measured_y))**2)
    
    mse_filtered = np.mean((np.array(true_x) - np.array(filtered_x))**2 + 
                           (np.array(true_y) - np.array(filtered_y))**2)

    print(f"Raw Measurement MSE: {mse_raw:.2f}")
    print(f"Filtered MSE:        {mse_filtered:.2f}")
    
    # Simple assertion to guarantee the filter actually reduced the noise
    assert mse_filtered < mse_raw, "Filter failed to smooth the trajectory!"
    print("Unit test passed: Filter successfully smoothed the trajectory.\n")

    # --- 6. Plot the Results ---
    plt.figure(figsize=(10, 6))
    plt.plot(true_x, true_y, 'g-', label='True Trajectory', linewidth=2)
    plt.scatter(measured_x, measured_y, c='r', marker='x', label='Noisy Measurements', alpha=0.6)
    plt.plot(filtered_x, filtered_y, 'b-', label='Kalman Filter Output', linewidth=2)
    
    plt.title('4-State Kalman Filter: Smoothing a Noisy 2D Trajectory')
    plt.xlabel('X Position')
    plt.ylabel('Y Position')
    plt.legend()
    plt.grid(True)
    plt.axis('equal')
    plt.show()

# Run the test
if __name__ == "__main__":
    run_kalman_unit_test()
