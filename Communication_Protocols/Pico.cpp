#include <Arduino.h>
#include <Servo.h>
#include <Adafruit_NeoPixel.h>

// --- Hardware Pin Definitions ---
#define PIN_SERVO_PITCH 2
#define PIN_SERVO_YAW 3
#define PIN_ESC_M1 4
#define PIN_ESC_M2 5
#define PIN_ESC_M3 6
#define PIN_NEOPIXEL 7
#define NUM_LEDS 100 // Total LEDs on your target screen

// --- Object Initialization ---
Servo pitchServo;
Servo yawServo;
Servo escM1, escM2, escM3; // ESCs are controlled identically to servos (using PWM)
Adafruit_NeoPixel strip(NUM_LEDS, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);

void setup() {
    Serial.begin(115200); // Must match Pi 5 baudrate
    
    // Attach hardware
    pitchServo.attach(PIN_SERVO_PITCH);
    yawServo.attach(PIN_SERVO_YAW);
    escM1.attach(PIN_ESC_M1);
    escM2.attach(PIN_ESC_M2);
    escM3.attach(PIN_ESC_M3);
    
    // Initialize LEDs
    strip.begin();
    strip.show(); // Initialize all pixels to 'off'
    
    // NOTE: Will need an "Arming Sequence" here for the BLDC ESCs 
    // (Usually sending a 1000us minimum throttle signal for 2 seconds).
}

void loop() {
    // Interrupt-driven Circular Buffer check
  
    if (Serial.available() > 0) {
        // Read the incoming bytes until the newline character is found
        String packet = Serial.readStringUntil('\n');
        
        // Ensure it's a valid "Shoot" command
        if (packet.charAt(0) == 'S') {
            parseAndExecute(packet);
        }
    }
}

void parseAndExecute(String packet) {
    // packet looks like: "S:15.5:0.0:2500:2500:3000:4"
    // "Shoot:Pitch:Yaw:Motor1_PWM:Motor2_PWM:Motor3_PWM:LED_trigger_Zone"
  
    // 1. Parse the string using sscanf (fastest C-style method)
    char header;
    float pitch, yaw;
    int m1_rpm, m2_rpm, m3_rpm, zone_id;
    
    // Extract the variables separated by colons
    sscanf(packet.c_str(), "%c:%f:%f:%d:%d:%d:%d", 
           &header, &pitch, &yaw, &m1_rpm, &m2_rpm, &m3_rpm, &zone_id);

    // 2. Map Math to Hardware (You will calibrate these ranges later)
    // Example: Map -45 to +45 degrees yaw to 45 to 135 servo degrees
    int mappedYaw = map(yaw, -45, 45, 45, 135); 
    yawServo.write(mappedYaw);
    
    int mappedPitch = map(pitch, 0, 35, 90, 125);
    pitchServo.write(mappedPitch);

    // 3. Map RPM to ESC PWM signal (You must use your Empirically Calibrated Table here)
    // escM1.writeMicroseconds( map(m1_rpm, 0, 5000, 1000, 2000) ); 
    
    // 4. Update the NeoPixels based on the Zone ID
    updateTargetLEDs(zone_id);
}

void updateTargetLEDs(int zone_id) {
    strip.clear(); // Turn off previous target
    
    // Example Zone Mapping
    if (zone_id == 1) {
        // Light up LEDs 0 through 9 in Red
        for(int i=0; i<10; i++) strip.setPixelColor(i, strip.Color(255, 0, 0));
    } else if (zone_id == 2) {
        // Light up LEDs 10 through 19 in Blue
        for(int i=10; i<20; i++) strip.setPixelColor(i, strip.Color(0, 0, 255));
    }
    
    strip.show(); // Push the color data to the actual strip
}
