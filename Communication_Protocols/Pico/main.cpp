#include <Arduino.h>
#include <Servo.h>
#include <Stepper.h>
#include <Adafruit_NeoPixel.h>

// ==============================================================================
// 1. HARDWARE PIN DEFINITIONS
// ==============================================================================
#define PIN_SERVO_PITCH 2
#define PIN_SERVO_YAW   3
#define PIN_ESC_M1      4
#define PIN_ESC_M2      5
#define PIN_ESC_M3      6
#define PIN_NEOPIXEL    7
#define PIN_IR_SENSOR   12

// ULN2003 Stepper Pins
#define STEP1 8
#define STEP2 9
#define STEP3 10
#define STEP4 11

// ==============================================================================
// 2. LED TARGET SCREEN CONFIGURATION
// ==============================================================================
const int NUM_COLS = 8;         // 8 Vertical Strips
const int ZONES_PER_COL = 4;    // 'n' zones per strip. Total Zones = 32
const int LEDS_PER_STRIP = 24;  // Assuming 40cm strip at 60 LEDs/meter
const int LEDS_PER_ZONE = LEDS_PER_STRIP / ZONES_PER_COL; // 6 LEDs per zone
const int TOTAL_LEDS = NUM_COLS * LEDS_PER_STRIP;         // 192 total LEDs

Adafruit_NeoPixel strip(TOTAL_LEDS, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);

// ==============================================================================
// 3. HARDCODED JSON CONFIGURATION (Kinematics & Motors)
// ==============================================================================
// Yaw Servo Mapping: [Min_Deg, Max_Deg, Min_PWM, Max_PWM]
const float YAW_MAP[4]   = {-45.0, 45.0, 45.0, 135.0}; 
// Pitch Servo Mapping: [Min_Deg, Max_Deg, Min_PWM, Max_PWM]
const float PITCH_MAP[4] = {0.0, 35.0, 90.0, 125.0};   

// Empirical RPM to PWM conversion table (1000kV A2212 motors, 12V, 15g flywheel)
const int RPM_ARRAY[] = {0, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000};
const int PWM_ARRAY[] = {1000, 1100, 1180, 1260, 1340, 1420, 1500, 1580, 1680, 1850, 2000};
const int RPM_ARRAY_SIZE = 11;

// ==============================================================================
// 4. GLOBAL OBJECTS
// ==============================================================================
Servo pitchServo;
Servo yawServo;
Servo escM1, escM2, escM3;
Stepper feederStepper(2048, STEP1, STEP3, STEP2, STEP4); // 2048 steps per rev

// ==============================================================================
// 5. HELPER FUNCTIONS
// ==============================================================================

// Linear Interpolation for Motor Speed
int interpolateRPMtoPWM(int target_rpm) {
    if (target_rpm <= RPM_ARRAY[0]) return PWM_ARRAY[0];
    if (target_rpm >= RPM_ARRAY[RPM_ARRAY_SIZE - 1]) return PWM_ARRAY[RPM_ARRAY_SIZE - 1];

    for (int i = 0; i < RPM_ARRAY_SIZE - 1; i++) {
        if (target_rpm >= RPM_ARRAY[i] && target_rpm <= RPM_ARRAY[i + 1]) {
            float slope = (float)(PWM_ARRAY[i+1] - PWM_ARRAY[i]) / (RPM_ARRAY[i+1] - RPM_ARRAY[i]);
            return PWM_ARRAY[i] + slope * (target_rpm - RPM_ARRAY[i]);
        }
    }
    return 1000; // Failsafe (Motor Off)
}

// LED Zone Logic (With Serpentine correction)
void updateTargetLEDs(int zone_id) {
    strip.clear(); 

    if (zone_id < 1 || zone_id > (NUM_COLS * ZONES_PER_COL)) {
        strip.show(); // Invalid zone, turn off LEDs
        return;
    }

    // Convert 1-indexed Zone to Row and Col (Reading left-to-right, top-to-bottom)
    int zero_indexed_zone = zone_id - 1;
    int col = zero_indexed_zone % NUM_COLS;
    int row = zero_indexed_zone / NUM_COLS;

    int physical_start_led = 0;

    // Serpentine Math: Even columns go Top-Down, Odd columns go Bottom-Up
    if (col % 2 == 0) {
        // Top-Down Strip
        physical_start_led = (col * LEDS_PER_STRIP) + (row * LEDS_PER_ZONE);
    } else {
        // Bottom-Up Strip (Reverse the row index)
        physical_start_led = (col * LEDS_PER_STRIP) + ((ZONES_PER_COL - 1 - row) * LEDS_PER_ZONE);
    }

    // Light up the calculated LED chunk in GREEN (R=0, G=255, B=0)
    for (int i = 0; i < LEDS_PER_ZONE; i++) {
        strip.setPixelColor(physical_start_led + i, strip.Color(0, 255, 0));
    }
    
    strip.show();
}

// Stepper Motor Push Logic
void feedBall() {
    int pushSteps = 683; // Approx 120 degrees
    bool ballDetected = false;

    // Move arm forward
    for (int i = 0; i < pushSteps; i++) {
        feederStepper.step(1); 
        
        // Check IR Sensor mid-push
        if (digitalRead(PIN_IR_SENSOR) == LOW && !ballDetected) {
            ballDetected = true;
            // Target acquired, you could trigger a confirmation beep/LED flash here
        }
        delay(2); // Needed for 28BYJ-48 to not slip gears
    }

    delay(200); // Allow ball to pass through flywheels

    // Retract arm back to starting position
    feederStepper.step(-pushSteps);
}

// ==============================================================================
// 6. MAIN EXECUTION LOGIC
// ==============================================================================

void executeShot(float pitch, float yaw, int m1_rpm, int m2_rpm, int m3_rpm, int zone_id) {
    // 1. Set the Target Screen
    updateTargetLEDs(zone_id);

    // 2. Map and Move Servos
    int p_servo = map(pitch, PITCH_MAP[0], PITCH_MAP[1], PITCH_MAP[2], PITCH_MAP[3]);
    int y_servo = map(yaw, YAW_MAP[0], YAW_MAP[1], YAW_MAP[2], YAW_MAP[3]);
    pitchServo.write(p_servo);
    yawServo.write(y_servo);

    // 3. Spin up the BLDC Motors
    escM1.writeMicroseconds(interpolateRPMtoPWM(m1_rpm));
    escM2.writeMicroseconds(interpolateRPMtoPWM(m2_rpm));
    escM3.writeMicroseconds(interpolateRPMtoPWM(m3_rpm));

    // Wait for the heavy flywheels to overcome inertia and reach target RPM
    delay(500); 

    // 4. Feed the ball into the wheels
    feedBall();

    // 5. Spin down motors safely to neutral (1000us)
    escM1.writeMicroseconds(1000);
    escM2.writeMicroseconds(1000);
    escM3.writeMicroseconds(1000);
    
    // Optional: clear the LED screen after the shot
    // strip.clear(); strip.show();
}

void setup() {
    Serial.begin(115200);

    // Attach Hardware
    pitchServo.attach(PIN_SERVO_PITCH);
    yawServo.attach(PIN_SERVO_YAW);
    escM1.attach(PIN_ESC_M1);
    escM2.attach(PIN_ESC_M2);
    escM3.attach(PIN_ESC_M3);
    pinMode(PIN_IR_SENSOR, INPUT_PULLUP);
    
    feederStepper.setSpeed(15); // Max reliable RPM for 28BYJ-48

    // Initialize LEDs
    strip.begin();
    strip.setBrightness(150); // Set brightness to ~60% to save power
    strip.clear();
    strip.show();

    // BLDC ARMING SEQUENCE
    // ESCs require a minimum throttle signal to initialize upon boot
    escM1.writeMicroseconds(1000);
    escM2.writeMicroseconds(1000);
    escM3.writeMicroseconds(1000);
    delay(3000); // 3 seconds wait is standard for A2212 ESCs
}

void loop() {
    // Interrupt-driven Serial Check
    if (Serial.available() > 0) {
        String packet = Serial.readStringUntil('\n');
        
        // Validate Header Character
        if (packet.charAt(0) == 'S') {
            char header;
            float pitch, yaw;
            int m1, m2, m3, zone_id;
            
            // Format: S:Pitch:Yaw:M1:M2:M3:ZoneID
            sscanf(packet.c_str(), "%c:%f:%f:%d:%d:%d:%d", 
                   &header, &pitch, &yaw, &m1, &m2, &m3, &zone_id);
            
            // Fire!
            executeShot(pitch, yaw, m1, m2, m3, zone_id);
        }
    }
}
