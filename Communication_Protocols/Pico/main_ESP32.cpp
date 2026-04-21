#include <Arduino.h>
#include <ESP32Servo.h>
#include <Adafruit_NeoPixel.h>

// ==========================================
// HARDWARE PINS & CONFIGURATION
// ==========================================
#define PIN_SERVO_PITCH 12
#define PIN_SERVO_YAW   13
#define PIN_ESC_M1      14
#define PIN_ESC_M2      27
#define PIN_ESC_M3      26
#define PIN_FEED_MOTOR  25  // Stepper or DC Motor for feeding
#define PIN_NEOPIXEL    33

#define MAX_LEDS 1000 
Adafruit_NeoPixel strip = Adafruit_NeoPixel(MAX_LEDS, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);

Servo pitchServo;
Servo yawServo;
Servo escM1, escM2, escM3;

// ==========================================
// 1. BALL FEEDING MECHANISM
// ==========================================
int feed_speed_pwm = 150;      // PWM value (0-255) for DC motor, or step delay for stepper
int feed_duration_ms = 500;    // How long the motor runs to feed exactly ONE ball
int feed_recovery_ms = 1500;   // Wait time after feeding for motors to recover RPM

void feedBall() {
     // FEED MATH & LOGIC:
     // If using a DC motor on a rotating Geneva drive / feed wheel:
     // Time to feed 1 ball = (60 / RPM) * (1 / slots_on_wheel) * 1000 in milliseconds.
     // Set feed_duration_ms to exactly match that mathematical rotation.
     
    // 1. Actuate feed motor
    analogWrite(PIN_FEED_MOTOR, feed_speed_pwm); 
    
    // 2. Wait for one ball to drop into the launcher
    delay(feed_duration_ms); 
    
    // 3. Stop feed motor
    analogWrite(PIN_FEED_MOTOR, 0); 
    
    // 4. Delay to let the flywheels recover kinetic energy before next shot
    delay(feed_recovery_ms); 
}

// ==========================================
// 2. SERPENTINE LED LOGIC
// ==========================================
void renderTargetZone(int target_zone) {
    // --- User Defined LED Variables ---
    float screen_height_cm = 100.0; 
    float screen_width_cm  = 150.0; 
    float led_length_cm    = 650.0; // Total length of the physical LED strip
    int leds_per_metre     = 60;    // Standard densities: 30, 60, or 144
    int total_zones        = 6;     // Total defined target grids    
    float leds_per_cm = leds_per_metre / 100.0;

    int num_vertical_strips = (int)((led_length_cm - screen_width_cm) / screen_height_cm);
    int leds_per_vertical = round(screen_height_cm * leds_per_cm);
    int leds_per_horizontal = round((screen_width_cm / (num_vertical_strips - 1)) * leds_per_cm);
    
    strip.clear();
    
    // --- Illumination Logic (Strictly skipping horizontal width strips) ---
    // Code lights up the entire vertical strip segment to visually 
    // indicate a zone. We can mathematically segment 'leds_per_vertical' later 
    // based on the exact Y-coordinate grids.
    
    int current_led_index = 0;
    
    for (int strip_idx = 0; strip_idx < num_vertical_strips; strip_idx++) {
        
        bool is_going_up = (strip_idx % 2 == 0); // Serpentine alternates direction

        // 1. Process Vertical Strip (We light these up!)
        for (int v = 0; v < leds_per_vertical; v++) {
            // Determine if this vertical strip belongs to the requested target_zone
            // Placeholder logic: Assigns strips to zones sequentially
            int assigned_zone = (strip_idx * total_zones) / num_vertical_strips; 
            
            if (assigned_zone == target_zone) {
                strip.setPixelColor(current_led_index, strip.Color(0, 255, 0)); // Green
            }
            current_led_index++;
        }
        
        // 2. Process Horizontal Connecting Strip (STRICTLY SKIPPED / OFF)
        if (strip_idx < num_vertical_strips - 1) {
            current_led_index += leds_per_horizontal;
        }
    }
    strip.show();
}

// ==========================================
// MAIN INITIALIZATION & LOOP
// ==========================================
void setup() {
    Serial.setRxBufferSize(1024); // Expanded the RX buffer to prevent Byte drop during transfer
    Serial.begin(921600);
    
    pinMode(PIN_FEED_MOTOR, OUTPUT);
    
    pitchServo.attach(PIN_SERVO_PITCH);
    yawServo.attach(PIN_SERVO_YAW);
    escM1.attach(PIN_ESC_M1, 1000, 2000); // Standard ESC min/max pulse
    escM2.attach(PIN_ESC_M2, 1000, 2000);
    escM3.attach(PIN_ESC_M3, 1000, 2000);
    
    strip.begin();
    strip.show(); // Initialize all pixels to 'off'
    
    // Arming ESCs (Send 0 throttle on boot)
    escM1.writeMicroseconds(1000);
    escM2.writeMicroseconds(1000);
    escM3.writeMicroseconds(1000);
    delay(2000); 
}

void loop() {
    // Wait for the Pi 5 to send a full command packet
    if (Serial.available()) {
        String packet = Serial.readStringUntil('\n');
        
        char header;
        float pitch, yaw;
        int m1_rpm, m2_rpm, m3_rpm, zone_id;
        
        // Parse (Number of items sucessful received): S:Pitch:Yaw:M1:M2:M3:ZoneID
        int parsed = sscanf(packet.c_str(), "%c:%f:%f:%d:%d:%d:%d", 
                            &header, &pitch, &yaw, &m1_rpm, &m2_rpm, &m3_rpm, &zone_id);
                            
        if (parsed == 7 && header == 'S') {
            
            // 1. Point the servos
            int mappedYaw = map(yaw, -45, 45, 45, 135); 
            yawServo.write(mappedYaw);
            
            int mappedPitch = map(pitch, 0, 35, 90, 125);
            pitchServo.write(mappedPitch);

            // 2. Spin up the flywheels (Assuming max 5000 RPM maps to 2000us max throttle)
            escM1.writeMicroseconds(map(m1_rpm, 0, 5000, 1000, 2000));
            escM2.writeMicroseconds(map(m2_rpm, 0, 5000, 1000, 2000));
            escM3.writeMicroseconds(map(m3_rpm, 0, 5000, 1000, 2000));

            // 3. Update the LED targeting grid
            renderTargetZone(zone_id);

            // Give motors a fraction of a second to reach Target RPM
            delay(500); 

            // 4. Feed the ball
            feedBall();
        }
    }
}
