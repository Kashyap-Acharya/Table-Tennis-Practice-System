import numpy as np

class ConstantVelocityKalmanFilter:
    def __init__(self, dt, std_acc, std_meas):
        """
        Initializes the 4-state 2D constant velocity Kalman filter.
        :param dt: Time step between frames/updates
        :param std_acc: Standard deviation of acceleration (process noise)
        :param std_meas: Standard deviation of measurement (sensor noise)
        """
        # State vector: [x, y, vx, vy]. Initialized to zeros.
        self.x = np.zeros((4, 1))

        # State Transition Matrix (F)
        self.F = np.array([[1, 0, dt, 0],
                           [0, 1, 0,  dt],
                           [0, 0, 1,  0],
                           [0, 0, 0,  1]])

        # Measurement Mapping Matrix (H) - We only observe x and y
        self.H = np.array([[1, 0, 0, 0],
                           [0, 1, 0, 0]])

        # Process Noise Covariance (Q) - Uncertainty in our constant velocity assumption
        # Calculated using the discrete noise model for acceleration
        self.Q = np.array([[(dt**4)/4, 0,         (dt**3)/2, 0],
                           [0,         (dt**4)/4, 0,         (dt**3)/2],
                           [(dt**3)/2, 0,         dt**2,     0],
                           [0,         (dt**3)/2, 0,         dt**2]]) * (std_acc**2)

        # Measurement Noise Covariance (R) - Sensor inaccuracy
        self.R = np.array([[std_meas**2, 0],
                           [0, std_meas**2]])

        # Initial Estimate Error Covariance (P)
        self.P = np.eye(4)

        # Identity Matrix (I)
        self.I = np.eye(4)

    def predict(self):
        """
        Prediction Phase: Calculates where the object should be in the next frame.
        Returns the predicted state [x, y, vx, vy]
        """
        # 1. Project the state ahead
        self.x = self.F @ self.x
        
        # 2. Project the error covariance ahead
        self.P = self.F @ self.P @ self.F.T + self.Q
        
        return self.x

    def update(self, z):
        """
        Correction (Update) Phase: Merges the prediction with a new observation.
        :param z: Measurement vector [x, y] as a 2x1 numpy array
        Returns the corrected state [x, y, vx, vy]
        """
        # 1. Calculate measurement residual (difference between observation and prediction)
        y = z - (self.H @ self.x)
        
        # 2. Calculate residual covariance
        S = self.H @ self.P @ self.H.T + self.R
        
        # 3. Calculate the Kalman Gain
        K = self.P @ self.H.T @ np.linalg.inv(S)
        
        # 4. Update the state estimate
        self.x = self.x + (K @ y)
        
        # 5. Update the error covariance
        self.P = (self.I - (K @ self.H)) @ self.P
        
        return self.x
