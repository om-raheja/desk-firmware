from machine import Pin, PWM
from rotary_irq_rp2 import RotaryIRQ
import time
import neopixel

# TODO: This is inefficient, use IRQs 

### Rotary Encoder ###

# We set the range depending on the mode
encoder = RotaryIRQ(pin_num_clk=2,
                    pin_num_dt=3,
                    min_val=0,        # Ignored when unbounded
                    max_val=0,        # Ignored when unbounded
                    reverse=True,
                    range_mode=RotaryIRQ.RANGE_UNBOUNDED)


sw = Pin(4, Pin.IN, Pin.PULL_UP)   # GP4

last_raw = encoder._value
button_pressed = False
last_button_state = sw.value()

### WS2812 LED ###
NUM_LEDS = 16
np = neopixel.NeoPixel(machine.Pin(5), NUM_LEDS) # GP5

# Colors (R, G, B)
RED = (255, 0, 0)     
GREEN = (0, 255, 0) 
BLUE = (0, 0, 255)
BLACK = (0, 0, 0)

ORANGE = (255, 165, 0)
SILVER = (192, 192, 192)
PURPLE = (128, 0, 128)

SUBTLE_GLOW = (254, 245, 193)
SUBTLE_GLOW_SELECTED = (255, 204, 74)

# Three LED to indicate mode you intend to choose
# led = (R, G, B)
edgelight_led = (PWM(Pin(9)), PWM(Pin(10)), PWM(Pin(11)))
motor_led = (PWM(Pin(13)), PWM(Pin(14)), PWM(Pin(15)))
focus_led = (PWM(Pin(6)), PWM(Pin(7)), PWM(Pin(8)))

### Haptic (Buzzer for Testing) ###
buzzer = PWM(Pin(28))

### COB LED Strip (One LED) ###
cob_led_strip = (PWM(Pin(18)), PWM(Pin(17)), PWM(Pin(16)))

for pwm_pin in edgelight_led + motor_led + focus_led + cob_led_strip:
    pwm_pin.freq(1000)  # 1000Hz for smooth dimming

cob_led_strip_brightness = 0

### 12V DC Motor (Servo Motor) ###
dc_motor = PWM(Pin(26))
dc_motor.freq(50)
motor_value = 0

motor_last_activity_time = 0
MOTOR_TIMEOUT_MS = 15000  # 15 seconds

try:
    with open("motor.txt", "r") as f:
        motor_value = int(f.read())
except OSError as e:
    print(f"File error: {e}")
    # Set a default value since the file doesn't exist or can't be read
    motor_value = 0
    print(f"Using default motor value: {motor_value}")
except ValueError as e:
    print(f"Data error: Could not convert file content to an integer.")
    motor_value = 0

class Mode:
    """A lightweight class to define modes with min/max values."""
    # Format: MODE_NAME = (id, max_val)
    HOME = (0, NUM_LEDS) # FOCUS, EDGELIGHT, MOTOR
    EDGELIGHT = (1, 101)
    MOTOR = (2, 101)
    FOCUS = (3, NUM_LEDS) # 30, 45, 60
    FOCUS_CONTROL = (4, 3) # Cancel, Stop, Back

### Mode Settings ###
current_mode = None

# Positions to encode Edgelight, Motor, Focus
class HomePos:
    EDGELIGHT = 0
    MOTOR = 6
    FOCUS = 11

# Positions to encode 30, 40, or 60 minutes
class FocusPos:
    M30 = 5
    M45 = 9
    M60 = 13

# Countdown variables
countdown_active = False
focus_time = 0  # in minutes
start_time = 0
remaining_time = 0  # in seconds
current_led_index = NUM_LEDS
leds_per_minute = 0

countdown_paused = False
paused_remaining_time = 0
paused_start_time = 0

mapped = 0

def home_init(from_motor=False):
    global current_mode
    current_mode = Mode.HOME
    set_np_color(SUBTLE_GLOW)
    print(f"COB Brightness in home_init: {cob_led_strip_brightness}")

    # TODO: don't set LEDs in this function to make code clean
    if from_motor:
        set_rgb(edgelight_led, BLACK)
        set_rgb(motor_led, GREEN)
        encoder._value = HomePos.MOTOR
    else:
        if cob_led_strip_brightness == 0:
            set_rgb(edgelight_led, GREEN)
        else:
            set_rgb(edgelight_led, BLUE)
        set_rgb(motor_led, BLACK)
        encoder._value = HomePos.EDGELIGHT

    set_rgb(focus_led, BLACK)

def set_rgb(led, rgb_tuple):
    led[0].duty_u16(rgb_tuple[0] * 257)
    led[1].duty_u16(rgb_tuple[1] * 257)
    led[2].duty_u16(rgb_tuple[2] * 257)

def set_cob_brightness(brightness):
    cob_led_strip[0].duty_u16(int(655.35 * brightness))
    cob_led_strip[1].duty_u16(int(655.35 * brightness))
    cob_led_strip[2].duty_u16(int(655.35 * brightness))

def focus_init():
    global current_mode
    current_mode = Mode.FOCUS
    np[0] = SUBTLE_GLOW_SELECTED
    np.write()
    set_rgb(focus_led, BLUE)
    encoder._value = 0

def focus_control_init():
    global current_mode
    current_mode = Mode.FOCUS_CONTROL
    set_np_color(SUBTLE_GLOW_SELECTED)
    encoder._value = 0

def edgelight_init():
    global current_mode
    current_mode = Mode.EDGELIGHT
    set_rgb(edgelight_led, BLUE)
    encoder._value = cob_led_strip_brightness

def motor_init():
    global current_mode
    current_mode = Mode.MOTOR
    set_rgb(motor_led, BLUE)
    encoder._value = motor_value
    motor_last_activity_time = time.ticks_ms()  

def set_servo_angle(angle):
    # Convert angle to a duty cycle between 1638 and 8192
    # These values are common for 0-180° servos; you may need to adjust them
    min_duty = 1638  # 0.5ms pulse (0 degrees)
    max_duty = 8192  # 2.5ms pulse (180 degrees)

    # we use 0-100 for 0-180 degrees
    duty = int(min_duty + (max_duty - min_duty) * angle / 100)
    dc_motor.duty_u16(duty)

# In start_focus_countdown function:
def start_focus_countdown(minutes):
    global countdown_active, start_time, remaining_time, current_mode
    global current_led_index, leds_per_minute, focus_time
    
    focus_time = minutes
    countdown_active = True
    countdown_paused = False 
    remaining_time = minutes * 60  # Convert to seconds
    start_time = time.ticks_ms()
    current_mode = Mode.HOME

    # Start with all LEDs black (OFF) - they'll fill with green over time
    for i in range(NUM_LEDS):
        np[i] = SUBTLE_GLOW
    np.write()
    set_rgb(focus_led, BLUE)
    
    print(f"Countdown started: {minutes} minutes")

# In update_countdown function:
def update_countdown():
    global remaining_time, current_led_index, countdown_active, start_time
    
    if not countdown_active or countdown_paused:
        return
    
    # Calculate elapsed time
    current_time = time.ticks_ms()
    elapsed_ms = time.ticks_diff(current_time, start_time)
    elapsed_seconds = elapsed_ms // 1000
    
    # Update remaining time
    remaining_time = (focus_time * 60) - elapsed_seconds
    
    if remaining_time <= 0:
        # Countdown finished - all LEDs green
        for i in range(NUM_LEDS):
            np[i] = GREEN
        np.write()
        
        countdown_active = False
        encoder._value = 0
        last_raw = 0
        mapped = 0
        
        # Flash completion pattern
        for _ in range(3):
            for i in range(NUM_LEDS):
                np[i] = GREEN
            np.write()
            time.sleep(0.3)
            for i in range(NUM_LEDS):
                np[i] = BLACK
            np.write()
            time.sleep(0.3)
        
        buzzer.freq(523)  # C5 note
        buzzer.duty_u16(32768)
        time.sleep(1)
        buzzer.duty_u16(0)
        
        print("Countdown completed!")
        home_init()
        return
    
    # Calculate how many LEDs should be green based on elapsed time
    # Progress from 0 to NUM_LEDS as time passes
    if current_mode != Mode.HOME:
        return 
    
    fractional_light_up(elapsed_seconds / (focus_time * 60))  # 0 to 1

# Add pause/resume function
def toggle_focus_pause():
    global countdown_paused, paused_start_time, paused_remaining_time, start_time
    
    if not countdown_active:
        return
    
    if countdown_paused:
        # Resume the countdown
        countdown_paused = False
        # Calculate how much time has passed while paused
        current_time = time.ticks_ms()
        pause_duration = time.ticks_diff(current_time, paused_start_time)
        # Adjust start_time by the pause duration
        start_time += pause_duration
        print(f"Countdown resumed. Remaining: {remaining_time} seconds")
        
        # Visual feedback - flash yellow/green
        for _ in range(2):
            for i in range(NUM_LEDS):
                np[i] = GREEN
            np.write()
            time.sleep(0.2)
            fractional_light_up(1 - (remaining_time / (focus_time * 60)))
            time.sleep(0.2)
    else:
        # Pause the countdown
        countdown_paused = True
        paused_start_time = time.ticks_ms()
        print(f"Countdown paused. Remaining: {remaining_time} seconds")
        
        # Visual feedback - flash orange
        for _ in range(3):
            for i in range(NUM_LEDS):
                np[i] = ORANGE
            np.write()
            time.sleep(0.2)
            for i in range(NUM_LEDS):
                np[i] = BLACK
            np.write()
            time.sleep(0.2)
        
        # Show paused state (all LEDs orange)
        for i in range(NUM_LEDS):
            np[i] = ORANGE
        np.write()

def set_np_color(color):
    for i in range(NUM_LEDS):
        np[i] = color
    np.write()
    
def fractional_light_up(progress):
    leds_to_light = progress * NUM_LEDS
    
    # Separate integer and fractional parts
    full_leds = int(leds_to_light)
    fractional_part = leds_to_light - full_leds
    
    # Update LEDs - fill over time
    for i in range(NUM_LEDS):
        if i < full_leds:
            # LEDs that are fully lit (past time)
            np[i] = SUBTLE_GLOW_SELECTED
        elif i == full_leds and full_leds < NUM_LEDS:
            # Current LED that's fading - make it dimmer based on fractional part
            r = int(SUBTLE_GLOW[0] + (SUBTLE_GLOW_SELECTED[0] - SUBTLE_GLOW[0]) * fractional_part)
            g = int(SUBTLE_GLOW[1] + (SUBTLE_GLOW_SELECTED[1] - SUBTLE_GLOW[1]) * fractional_part)
            b = int(SUBTLE_GLOW[2] + (SUBTLE_GLOW_SELECTED[2] - SUBTLE_GLOW[2]) * fractional_part)
            np[i] = (r, g, b) 
        else:
            # Future LEDs - still black
            np[i] = SUBTLE_GLOW
    np.write()

def stop_focus_countdown():
    global countdown_active, current_mode, last_raw, mapped
    
    countdown_active = False
    countdown_paused = False

    encoder._value = 0
    last_raw = 0
    mapped = 0
    
    # Clear all LEDs
    for i in range(NUM_LEDS):
        np[i] = BLACK
    np.write()

    # Flash red to indicate stop
    for i in range(NUM_LEDS):
        np[i] = RED
    np.write()
    time.sleep(0.5)
    for i in range(NUM_LEDS):
        np[i] = BLACK
    np.write()
    
    buzzer.freq(330)
    buzzer.duty_u16(32768)
    time.sleep(0.3)
    buzzer.duty_u16(0)
    
    print("Countdown stopped")
    home_init()

def flash_blue():
    for i in range(NUM_LEDS):
        np[i] = ORANGE
    np.write()

    time.sleep(0.3)

    for i in range(NUM_LEDS):
        np[i] = BLACK
    
    np.write()

    time.sleep(0.3)

    # TODO: replace with a haptic in the actual device
    # Play a note (A4 = 440 Hz) for 0.5 seconds
    buzzer.freq(440)  # Set the frequency
    buzzer.duty_u16(32768)  # Set volume (50% duty cycle)
    time.sleep(0.5)

    # Stop the sound
    buzzer.duty_u16(0)

home_init()

while True:
    # --- Handle Countdown Updates ---
    if countdown_active:
        update_countdown()

    if current_mode == Mode.MOTOR:
        current_time = time.ticks_ms()
        # Check if 15 seconds have passed without activity
        if time.ticks_diff(current_time, motor_last_activity_time) >= MOTOR_TIMEOUT_MS:
            print("Motor mode timeout - returning to home")
            with open("motor.txt", "w") as f:
                f.write(str(motor_value))
            home_init(from_motor=True)
            # Skip the rest of the loop iteration to avoid processing stale encoder data
            continue

    # --- 1. HANDLE ENCODER ROTATION ---
    raw_pos = encoder._value  # Unconstrained
    
    if raw_pos != last_raw:
        _, max_val = current_mode 

        
        if current_mode == Mode.HOME:
            mapped = raw_pos % max_val

            # FOCUS, EDGELIGHT, MOTOR
            if mapped >= HomePos.FOCUS:
                if countdown_active:
                    set_rgb(focus_led, BLUE)
                else:
                    set_rgb(focus_led, GREEN)
                set_rgb(motor_led, BLACK)
                set_rgb(edgelight_led, BLACK)
            elif mapped >= HomePos.MOTOR:
                set_rgb(focus_led, BLACK)
                set_rgb(motor_led, GREEN)
                set_rgb(edgelight_led, BLACK)
            else:
                set_rgb(focus_led, BLACK)
                set_rgb(motor_led, BLACK)
                if cob_led_strip_brightness == 0:
                    set_rgb(edgelight_led, GREEN)
                else:
                    set_rgb(edgelight_led, BLUE)

        elif current_mode == Mode.FOCUS:
            mapped = raw_pos % max_val + 1
            
            if mapped in (FocusPos.M30, FocusPos.M45, FocusPos.M60):
                flash_blue()
            
            for i in range(NUM_LEDS):
                if i < mapped:
                    np[i] = SUBTLE_GLOW_SELECTED
                else:
                    np[i] = SUBTLE_GLOW

            np.write()

        elif current_mode == Mode.FOCUS_CONTROL:
            mapped = raw_pos % max_val

            if mapped == 0:
                set_np_color(SUBTLE_GLOW_SELECTED)
            elif mapped == 1:
                if countdown_paused:
                    set_np_color(PURPLE)
                else:
                    set_np_color(SILVER)
            else:
                set_np_color(RED)

            # pause (silver)
            # cancel (red)
            # preamble menu focus ke liye
        elif current_mode == Mode.EDGELIGHT:
            # 0-100%
            cob_led_strip_brightness = mapped = raw_pos % max_val
            fractional_light_up(mapped / 100)
            set_cob_brightness(cob_led_strip_brightness)
        else:
            motor_value = mapped = raw_pos % max_val
            set_servo_angle(motor_value)
            fractional_light_up(mapped / 100)
            motor_last_activity_time = time.ticks_ms()
        
        print(f"Mapped: {mapped} ; COB Brightness: {cob_led_strip_brightness}")
        last_raw = raw_pos
        
    # Check button press (active low)
    if sw.value() == 0:
        print("Button Pressed!")

        if current_mode == Mode.HOME:
            if mapped >= HomePos.FOCUS:
                if remaining_time > 0:
                    focus_control_init()
                else:
                    focus_init()
            elif mapped >= HomePos.MOTOR:
                motor_init()
            else:
                edgelight_init()
        elif current_mode == Mode.EDGELIGHT:
            home_init()
        elif current_mode == Mode.MOTOR:
            with open("motor.txt", "w") as f:
                f.write(str(motor_value))
            
            home_init(from_motor=True)

        elif current_mode == Mode.FOCUS:
            # TODO: agar countdown hai fir PAUSE + RESET
            if mapped == FocusPos.M30:
                start_focus_countdown(0.5)
            elif mapped == FocusPos.M45:
                start_focus_countdown(45)
            elif mapped == FocusPos.M60:
                start_focus_countdown(60)

            print(f"New Focus Time: {focus_time}")
        
        else: # current_mode == Mode.FOCUS_CONTROL
            if mapped == 0:
                current_mode = Mode.HOME
                encoder._value = HomePos.FOCUS
                mapped = HomePos.FOCUS
                set_rgb(edgelight_led, BLACK)
                set_rgb(motor_led, BLACK)
                set_rgb(focus_led, BLUE)
            elif mapped == 1:
                toggle_focus_pause()
            else:
                stop_focus_countdown()

        time.sleep(0.3)  # Simple debounce delay
    
    time.sleep(0.01)  # Short delay to prevent CPU overuse

buzzer.deinit()