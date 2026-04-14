#include <Arduino.h>
#include <ESP32Servo.h> // ESP32 CHANGE: Replaced standard <Servo.h> with ESP32-specific library
#include <Stepper.h>
#include <Adafruit_NeoPixel.h>

// ==============================================================================
// 1. HARDWARE PIN DEFINITIONS (ESP32 CHANGE: Remapped to Safe GPIOs)
// ==============================================================================
// Note: Pins 6-11 on the ESP32 are connected to internal flash memory. 
// Using them will cause the board to instantly crash and reboot.
#define PIN_SERVO_PITCH 13
#define PIN_SERVO_YAW   14
#define PIN_ESC_M1      25
#define PIN_ESC_M2      26
#define PIN_ESC_M3      27
#define PIN_NEOPIXEL    32
#define PIN_IR_SENSOR   33

// ULN2003 Stepper Pins (ESP32 CHANGE: Remapped to safe digital output pins)
#define STEP1 18
#define STEP2 19
#define STEP3 21
#define STEP4 22

// ==============================================================================
// 2. LED TARGET SCREEN CONFIGURATION
// ==============================================================================
const int NUM_COLS = 8;         
const int ZONES_PER_COL = 4;    
const int LEDS_PER_STRIP = 24;  
const int LEDS_PER_ZONE = LEDS_PER_STRIP / ZONES_PER_COL; 
const int TOTAL_LEDS = NUM_COLS * LEDS_PER_STRIP;         

Adafruit_NeoPixel strip(TOTAL_LEDS, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);

// ==============================================================================
// 3. HARDCODED JSON CONFIGURATION (Kinematics & Motors)
// ==============================================================================
const float YAW_MAP[4]   = {-45.0, 45.0, 45.0, 135.0}; 
const float PITCH_MAP[4] = {0.0, 35.0, 90.0, 125.0};   

const int RPM_ARRAY[] = {0, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000};
const int PWM_ARRAY[] = {1000, 1100, 1180, 1260, 1340, 1420, 1500, 1580, 1680, 1850, 2000};
const int RPM_ARRAY_SIZE = 11;

// ==============================================================================
// 4. GLOBAL OBJECTS
// ==============================================================================
Servo pitchServo;
Servo yawServo;
Servo escM1, escM2, escM3;
Stepper feederStepper(2048, STEP1, STEP3, STEP2, STEP4); 

// ==============================================================================
// 5. HELPER FUNCTIONS
// ==============================================================================

int interpolateRPMtoPWM(int target_rpm) {
    if (target_rpm <= RPM_ARRAY[0]) return PWM_ARRAY[0];
    if (target_rpm >= RPM_ARRAY[RPM_ARRAY_SIZE - 1]) return PWM_ARRAY[RPM_ARRAY_SIZE - 1];

    for (int i = 0; i < RPM_ARRAY_SIZE - 1; i++) {
        if (target_rpm >= RPM_ARRAY[i] && target_rpm <= RPM_ARRAY[i + 1]) {
            float slope = (float)(PWM_ARRAY[i+1] - PWM_ARRAY[i]) / (RPM_ARRAY[i+1] - RPM_ARRAY[i]);
            return PWM_ARRAY[i] + slope * (target_rpm - RPM_ARRAY[i]);
        }
    }
    return 1000; 
}

void updateTargetLEDs(int zone_id) {
    strip.clear(); 

    if (zone_id < 1 || zone_id > (NUM_COLS * ZONES_PER_COL)) {
        strip.show(); 
        return;
    }

    int zero_indexed_zone = zone_id - 1;
    int col = zero_indexed_zone % NUM_COLS;
    int row = zero_indexed_zone / NUM_COLS;

    int physical_start_led = 0;

    if (col % 2 == 0) {
        physical_start_led = (col * LEDS_PER_STRIP) + (row * LEDS_PER_ZONE);
    } else {
        physical_start_led = (col * LEDS_PER_STRIP) + ((ZONES_PER_COL - 1 - row) * LEDS_PER_ZONE);
    }

    for (int i = 0; i < LEDS_PER_ZONE; i++) {
        strip.setPixelColor(physical_start_led + i, strip.Color(0, 255, 0));
    }
    
    strip.show();
}

void feedBall() {
    int pushSteps = 683; 
    bool ballDetected = false;

    for (int i = 0; i < pushSteps; i++) {
        feederStepper.step(1); 
        
        if (digitalRead(PIN_IR_SENSOR) == LOW && !ballDetected) {
            ballDetected = true;
        }
        delay(2); 
    }

    delay(200); 
    feederStepper.step(-pushSteps);
}

void resetToDefault() {
    strip.clear();
    strip.show();

    escM1.writeMicroseconds(1000);
    escM2.writeMicroseconds(1000);
    escM3.writeMicroseconds(1000);

    int p_servo = map(0.0, PITCH_MAP[0], PITCH_MAP[1], PITCH_MAP[2], PITCH_MAP[3]);
    int y_servo = map(0.0, YAW_MAP[0], YAW_MAP[1], YAW_MAP[2], YAW_MAP[3]);
    pitchServo.write(p_servo);
    yawServo.write(y_servo);
}

// ==============================================================================
// 6. MAIN EXECUTION LOGIC
// ==============================================================================

void executeShot(float pitch, float yaw, int m1_rpm, int m2_rpm, int m3_rpm, int zone_id) {
    updateTargetLEDs(zone_id);

    int p_servo = map(pitch, PITCH_MAP[0], PITCH_MAP[1], PITCH_MAP[2], PITCH_MAP[3]);
    int y_servo = map(yaw, YAW_MAP[0], YAW_MAP[1], YAW_MAP[2], YAW_MAP[3]);
    pitchServo.write(p_servo);
    yawServo.write(y_servo);

    escM1.writeMicroseconds(interpolateRPMtoPWM(m1_rpm));
    escM2.writeMicroseconds(interpolateRPMtoPWM(m2_rpm));
    escM3.writeMicroseconds(interpolateRPMtoPWM(m3_rpm));

    delay(500); 

    feedBall();

    escM1.writeMicroseconds(1000);
    escM2.writeMicroseconds(1000);
    escM3.writeMicroseconds(1000);
}

void setup() {
    Serial.begin(115200);

    // ESP32 CHANGE: Allocate hardware timers for the ESP32 PWM signals to prevent jitter.
    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);
    ESP32PWM::allocateTimer(2);
    ESP32PWM::allocateTimer(3);

    pitchServo.attach(PIN_SERVO_PITCH);
    yawServo.attach(PIN_SERVO_YAW);
    escM1.attach(PIN_ESC_M1);
    escM2.attach(PIN_ESC_M2);
    escM3.attach(PIN_ESC_M3);
    pinMode(PIN_IR_SENSOR, INPUT_PULLUP);
    
    feederStepper.setSpeed(15); 

    strip.begin();
    strip.setBrightness(150); 
    strip.clear();
    strip.show();

    escM1.writeMicroseconds(1000);
    escM2.writeMicroseconds(1000);
    escM3.writeMicroseconds(1000);
    delay(3000); 
}

void loop() {
    if (Serial.available() > 0) {
        String packet = Serial.readStringUntil('\n');
        
        // ESP32 CHANGE: Cleaned up the redundant if-statement block for the 'S' character
        if (packet.charAt(0) == 'S') {
            char header;
            float pitch, yaw;
            int m1, m2, m3, zone_id;
            
            sscanf(packet.c_str(), "%c:%f:%f:%d:%d:%d:%d", 
                   &header, &pitch, &yaw, &m1, &m2, &m3, &zone_id);
            
            executeShot(pitch, yaw, m1, m2, m3, zone_id);
        } 
        else if (packet.charAt(0) == 'D') {
            resetToDefault();
        }
    }
}
