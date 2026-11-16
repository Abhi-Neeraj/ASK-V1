// Stand-in for ESP-IDF's esp_random.h (components/esp_hw_support/include/esp_random.h). The real header declares
// esp_random(); neither <Arduino.h> nor <esp_system.h> includes it in ESP-IDF 5.5, so a source file that calls
// esp_random() must include this one.
#pragma once
#include <stdint.h>

inline uint32_t esp_random() { return 4; }
