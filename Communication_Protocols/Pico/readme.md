# WOE Table Tennis Launcher - Low-Level Pico Controller

This directory contains the C++ codebase for the Raspberry Pi Pico. The Pico acts as the hard real-time executor for the launcher. It receives parsed ballistic data from the Raspberry Pi 5 (via UART), translates it into PWM signals, controls the NeoPixel target matrix, and manages the electromechanical firing sequence.

## 1. Physical Configuration Checklist (To-Do Before Flight)
*Because this code was written concurrently with the hardware build, several variables use placeholder values. **You must calibrate the following physical constants before live operation.***

### Hardware Pins
- [ ] Check and update `#define` assignments for Servos (`PIN_SERVO_PITCH`, `PIN_SERVO_YAW`).
- [ ] Check and update `#define` assignments for ESCs (`PIN_ESC_M1`, `M2`, `M3`).
- [ ] Check and update `#define` assignments for the Stepper/IR (`STEP1` to `STEP4`, `PIN_IR_SENSOR`).
- [ ] Check and update the NeoPixel pin (`PIN_NEOPIXEL`).

### Kinematics & Motors
- [ ] **Servo Mapping:** Calibrate `PITCH_MAP` and `YAW_MAP`. You need to find the exact PWM microsecond values (or Arduino `Servo.write()` degree equivalents) that correspond to your physical 0° to 35° pitch and -45° to +45° yaw.
- [ ] **RPM Conversion Array:** Calibrate `RPM_ARRAY` and `PWM_ARRAY`. You must use a tachometer to measure the actual RPM of your 1000kV flywheels at various PWM intervals (1000 to 2000us) under load and update this table.
- [ ] **ESC Arming Sequence:** Verify the delay needed in `setup()` for your specific A2212 ESCs. Currently set to a standard 3000ms at 1000us.

### Feeding Mechanism
- [ ] **Stepper Push Distance:** In `feedBall()`, update the `pushSteps` variable. It is currently set to 683 (roughly 120° for a 2048-step motor). Find the exact step count needed to push the ball fully into the flywheels.
- [ ] **IR Sensor State:** Confirm if your IR beam breaker reads `LOW` or `HIGH` when the beam is broken, and update the `digitalRead` check in `feedBall()` accordingly.

### Target Screen (LEDs)
- [x] Verify `NUM_COLS` (Vertical strips).
- [ ] Verify `ZONES_PER_COL` (How many hit-zones per strip).
- [x] Verify `LEDS_PER_STRIP` (How many physical LEDs on a single vertical track).

---

## 2. Code Structure & Core Functions

The software relies on an interrupt-driven, non-blocking architecture using the `Serial` ring buffer to ensure PWM timing is never interrupted by incoming data.

* `setup()`: Initializes hardware attachments, sets up the LED strip, and executes the mandatory minimum-throttle arming sequence for the BLDC ESCs.
* `loop()`: Polling loop that checks the UART buffer. If a newline `\n` is detected, it reads the packet and branches based on the header char (`S` for Shoot, `D` for Default/Stop).
* `interpolateRPMtoPWM(int target_rpm)`: A mathematical helper function. It uses the empirically derived `RPM_ARRAY` and calculates the exact PWM signal required using Linear Interpolation.
* `updateTargetLEDs(int zone_id)`: Maps the integer `zone_id` requested by the Pi 5 to the physical matrix. Includes "Serpentine Math" to account for the zig-zag wiring of the LED strips.
* `feedBall()`: Actuates the 28BYJ-48 stepper motor to push the ball. It steps forward iteratively, polling the IR sensor to confirm the ball has passed, pauses, and retracts to the neutral position.
* `executeShot(...)`: The main sequencer. It orchestrates the hardware by calling the above functions in order: Sets LEDs $\rightarrow$ Aims Servos $\rightarrow$ Spools up Flywheels $\rightarrow$ Waits for inertia (500ms) $\rightarrow$ Feeds Ball $\rightarrow$ Spins down Flywheels.
* `resetToDefault()`: Emergency stop / reset function. Triggered by a 'D' packet. Turns off all LEDs, kills motor throttle, and centers the servos to 0° Pitch / 0° Yaw.

---

## 3. Overall Progress

**Status:** `Architecture Complete | Awaiting Physical Calibration`

**What is Done:**
- [x] Non-blocking UART string parsing and buffer management.
- [x] Serpentine matrix math for the NeoPixel target screen.
- [x] BLDC arming logic and custom RPM-to-PWM linear interpolation.
- [x] Electromechanical sequencing (Aim -> Spin -> Delay -> Feed -> Reset).
- [x] Default/Stop override functionality.

**What is Left (To be done by the Hardware Team):**
- [ ] Tachometer mapping of the A2212 motors.
- [ ] Physical measurement of servo limit boundaries to prevent mechanical binding.
- [ ] Wiring and integration of the IR beam breaker logic.
- [ ] Final field testing with Pi 5 communications.
