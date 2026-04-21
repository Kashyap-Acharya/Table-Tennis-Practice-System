# T4-Trainer

# WOE // Table Tennis Practice System

An automated, intelligent table tennis training system built for the World of Engineering course. This system utilizes high-speed computer vision to track ball impacts, a physics-driven launcher engine to generate realistic drills, and an ultra-low-latency web interface for real-time user feedback.

## High-Level Architecture

The system utilizes a "Two-Brain" hardware architecture to ensure high-speed vision tasks do not interfere with time-critical motor and LED controls. We have implemented an elaborate IPC (Intra-process Communication) network as well to ensure that various processes are able to communicate with each other in real-time.

### Brain 1: Raspberry Pi 5 (The Master Controller)
Runs a customized, multi-process Python environment to bypass the Global Interpreter Lock (GIL), distributing tasks across multiple CPU cores. 

**Process 1: Vision Engine (CPU-Bound)**
* Captures 120fps monochrome video via a **ADD CAMERA NAME**.
* Applies Gaussian blurring, OpenCV contour detection, Kalman filtering, and Homography to calculate exact physical hit coordinates on the target board.
* **IPC:** Reads the `current_target` state from a **Shared Memory** block and writes the final `hit_recorded` coordinates back to it for ultra-low-latency access.

**Process 2: Web Server (I/O-Bound)**
* A lightweight FastAPI backend that serves a Vanilla JS/HTML5 frontend over an internal Wi-Fi hotspot. 
* **IPC (Command):** Receives user drill selections (e.g., Drill ID: 4) via WebSocket and pushes them into a thread-safe `multiprocessing.Queue` (`Command_Queue`).
* **IPC (Feedback):** Constantly monitors the Vision Engine's **Shared Memory** block. When a hit is registered, it instantly pushes the JSON coordinates to the user's phone via WebSocket.

**Process 3: Launcher Engine (Math & Hardware)**
* Acts as the physics engine and hardware bridge, remaining entirely decoupled from the web server.
* **IPC:** Listens to the `Command_Queue`. When a drill integer arrives, it wakes up and maps it to a local dictionary of physical parameters (X, Y, Velocity, Spin).
* Applies a statistical randomizer (Gaussian distribution) to the parameters to simulate realistic human variation within the target zone.
* Executes kinematic matrix calculations to convert physical targets into exact motor RPMs and servo angles.
* **Hardware Output:** Formats the final values into an ASCII string (e.g., `L:180\nA:45:12\nH:5\n`) and fires it over the USB UART connection to the Pico.

### Brain 2: ESP32 CP2102 (The Hardware Driver)
Runs bare-metal C++ to handle real-world physics:
* Listens to the Pi via a 921,600 baud USB UART connection (`/dev/ttyACM0`).
* Utilizes a hardware UART interrupt and a Circular Buffer (Ring Buffer) to parse incoming serial strings asynchronously, ensuring zero disruption to motor PWM timing.
* Controls the launcher motors, aiming servos, and uses PIO state machines to drive the WS2812B target zone LEDs.
