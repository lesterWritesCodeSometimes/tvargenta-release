// TVArgenta - encoder + LED de estado en GPIO25
// Compila con libgpiod 2.x: gcc -O2 -o encoder_reader encoder_reader.c -lgpiod
// LED ON mientras el proceso está vivo; OFF al salir (CTRL+C o shutdown).
// Wiring: LED -> GPIO25, otra pata -> GND.

#include <gpiod.h>
#include <stdio.h>
#include <unistd.h>
#include <stdlib.h>
#include <time.h>
#include <string.h>
#include <signal.h>

#define CHIP_PATH "/dev/gpiochip0"

// GPIO pin definitions
#define PIN_NEXT 3
#define PIN_CLK  23
#define PIN_DT   17
#define PIN_SW   27
#define PIN_LED  25

// Line indices within our requests
#define IDX_CLK  0
#define IDX_DT   1
#define IDX_SW   2
#define IDX_NEXT 3

static struct gpiod_chip *chip = NULL;
static struct gpiod_line_request *input_request = NULL;
static struct gpiod_line_request *led_request = NULL;
static volatile sig_atomic_t running = 1;

static void cleanup(void) {
    // Turn off LED and release resources
    if (led_request) {
        gpiod_line_request_set_value(led_request, PIN_LED, GPIOD_LINE_VALUE_INACTIVE);
        gpiod_line_request_release(led_request);
        led_request = NULL;
    }
    if (input_request) {
        gpiod_line_request_release(input_request);
        input_request = NULL;
    }
    if (chip) {
        gpiod_chip_close(chip);
        chip = NULL;
    }
}

static void on_signal(int sig) {
    (void)sig;
    running = 0;
}

int main(void) {
    // Signal handlers for clean shutdown (turns off LED)
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    // Open GPIO chip
    chip = gpiod_chip_open(CHIP_PATH);
    if (!chip) {
        perror("gpiod_chip_open");
        return 1;
    }

    // === Setup INPUT lines (CLK, DT, SW, NEXT) ===

    // Create settings for inputs without pull-up (CLK, DT)
    struct gpiod_line_settings *input_settings = gpiod_line_settings_new();
    if (!input_settings) {
        perror("gpiod_line_settings_new (input)");
        cleanup();
        return 1;
    }
    gpiod_line_settings_set_direction(input_settings, GPIOD_LINE_DIRECTION_INPUT);

    // Create settings for inputs with pull-up (SW, NEXT)
    struct gpiod_line_settings *pullup_settings = gpiod_line_settings_new();
    if (!pullup_settings) {
        perror("gpiod_line_settings_new (pullup)");
        gpiod_line_settings_free(input_settings);
        cleanup();
        return 1;
    }
    gpiod_line_settings_set_direction(pullup_settings, GPIOD_LINE_DIRECTION_INPUT);
    gpiod_line_settings_set_bias(pullup_settings, GPIOD_LINE_BIAS_PULL_UP);

    // Create line config
    struct gpiod_line_config *input_line_config = gpiod_line_config_new();
    if (!input_line_config) {
        perror("gpiod_line_config_new (input)");
        gpiod_line_settings_free(input_settings);
        gpiod_line_settings_free(pullup_settings);
        cleanup();
        return 1;
    }

    // Add CLK and DT without pull-up
    unsigned int no_pullup_offsets[] = {PIN_CLK, PIN_DT};
    if (gpiod_line_config_add_line_settings(input_line_config, no_pullup_offsets, 2, input_settings) < 0) {
        perror("gpiod_line_config_add_line_settings (clk/dt)");
        gpiod_line_config_free(input_line_config);
        gpiod_line_settings_free(input_settings);
        gpiod_line_settings_free(pullup_settings);
        cleanup();
        return 1;
    }

    // Add SW and NEXT with pull-up
    unsigned int pullup_offsets[] = {PIN_SW, PIN_NEXT};
    if (gpiod_line_config_add_line_settings(input_line_config, pullup_offsets, 2, pullup_settings) < 0) {
        perror("gpiod_line_config_add_line_settings (sw/next)");
        gpiod_line_config_free(input_line_config);
        gpiod_line_settings_free(input_settings);
        gpiod_line_settings_free(pullup_settings);
        cleanup();
        return 1;
    }

    // Create request config
    struct gpiod_request_config *input_req_config = gpiod_request_config_new();
    if (!input_req_config) {
        perror("gpiod_request_config_new (input)");
        gpiod_line_config_free(input_line_config);
        gpiod_line_settings_free(input_settings);
        gpiod_line_settings_free(pullup_settings);
        cleanup();
        return 1;
    }
    gpiod_request_config_set_consumer(input_req_config, "encoder");

    // Request input lines
    input_request = gpiod_chip_request_lines(chip, input_req_config, input_line_config);

    // Clean up config objects (no longer needed after request)
    gpiod_request_config_free(input_req_config);
    gpiod_line_config_free(input_line_config);
    gpiod_line_settings_free(input_settings);
    gpiod_line_settings_free(pullup_settings);

    if (!input_request) {
        perror("gpiod_chip_request_lines (input)");
        cleanup();
        return 1;
    }

    // === Setup OUTPUT line (LED) ===

    struct gpiod_line_settings *led_settings = gpiod_line_settings_new();
    if (!led_settings) {
        perror("gpiod_line_settings_new (led)");
        cleanup();
        return 1;
    }
    gpiod_line_settings_set_direction(led_settings, GPIOD_LINE_DIRECTION_OUTPUT);
    gpiod_line_settings_set_output_value(led_settings, GPIOD_LINE_VALUE_ACTIVE);  // LED ON at start

    struct gpiod_line_config *led_line_config = gpiod_line_config_new();
    if (!led_line_config) {
        perror("gpiod_line_config_new (led)");
        gpiod_line_settings_free(led_settings);
        cleanup();
        return 1;
    }

    unsigned int led_offset = PIN_LED;
    if (gpiod_line_config_add_line_settings(led_line_config, &led_offset, 1, led_settings) < 0) {
        perror("gpiod_line_config_add_line_settings (led)");
        gpiod_line_config_free(led_line_config);
        gpiod_line_settings_free(led_settings);
        cleanup();
        return 1;
    }

    struct gpiod_request_config *led_req_config = gpiod_request_config_new();
    if (!led_req_config) {
        perror("gpiod_request_config_new (led)");
        gpiod_line_config_free(led_line_config);
        gpiod_line_settings_free(led_settings);
        cleanup();
        return 1;
    }
    gpiod_request_config_set_consumer(led_req_config, "tvargenta-led");

    // Request LED line
    led_request = gpiod_chip_request_lines(chip, led_req_config, led_line_config);

    // Clean up config objects
    gpiod_request_config_free(led_req_config);
    gpiod_line_config_free(led_line_config);
    gpiod_line_settings_free(led_settings);

    if (!led_request) {
        perror("gpiod_chip_request_lines (led)");
        cleanup();
        return 1;
    }

    // Read initial values
    int last_clk = (gpiod_line_request_get_value(input_request, PIN_CLK) == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;
    int last_sw = (gpiod_line_request_get_value(input_request, PIN_SW) == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;
    int last_next = (gpiod_line_request_get_value(input_request, PIN_NEXT) == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;

    int sw_pressed = 0;
    int sw_released = 0;
    struct timespec ts_now;
    double last_next_fire = 0.0;
    const double NEXT_DEBOUNCE = 1.0;  // 1 second debounce

    // Main loop: emit ROTARY and BTN_* events to stdout
    while (running) {
        enum gpiod_line_value clk_raw = gpiod_line_request_get_value(input_request, PIN_CLK);
        enum gpiod_line_value dt_raw = gpiod_line_request_get_value(input_request, PIN_DT);
        enum gpiod_line_value sw_raw = gpiod_line_request_get_value(input_request, PIN_SW);

        int clk_val = (clk_raw == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;
        int dt_val = (dt_raw == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;
        int sw_val = (sw_raw == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;

        // ROTARY encoder
        if (clk_val != last_clk) {
            if (clk_val == 0) {  // Falling edge
                if (dt_val != clk_val)
                    printf("ROTARY_CW\n");
                else
                    printf("ROTARY_CCW\n");
                fflush(stdout);
            }
            last_clk = clk_val;
        }

        // BUTTON (encoder push)
        if (sw_val != last_sw) {
            if (sw_val == 0 && !sw_pressed) {
                printf("BTN_PRESS\n");
                fflush(stdout);
                sw_pressed = 1;
                sw_released = 0;
            } else if (sw_val == 1 && !sw_released && sw_pressed) {
                printf("BTN_RELEASE\n");
                fflush(stdout);
                sw_pressed = 0;
                sw_released = 1;
            }
            last_sw = sw_val;
        }

        // NEXT button on GPIO3 (active low with pull-up) with 1s debounce
        enum gpiod_line_value next_raw = gpiod_line_request_get_value(input_request, PIN_NEXT);
        int next_val = (next_raw == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;

        if (next_val != last_next) {
            // Falling edge = PRESSED (active-low)
            if (next_val == 0) {
                clock_gettime(CLOCK_MONOTONIC, &ts_now);
                double now_s = ts_now.tv_sec + ts_now.tv_nsec / 1e9;

                if ((now_s - last_next_fire) >= NEXT_DEBOUNCE) {
                    printf("BTN_NEXT\n");
                    fflush(stdout);
                    last_next_fire = now_s;
                }
            }
            last_next = next_val;
        }

        usleep(3000);  // 3 ms polling interval
    }

    // On exit: turn off LED and release resources
    cleanup();
    return 0;
}
