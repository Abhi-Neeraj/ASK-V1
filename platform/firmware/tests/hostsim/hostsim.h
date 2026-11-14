// SIMULATED HARDWARE for running the real firmware sources on a computer.
//
// This is not the ESP32. It is a set of stand-ins for the Arduino core, FreeRTOS, the memory card, the microphone,
// Wi-Fi and the HTTP client, written so the firmware's own files (storage, sdwriter, recorder, uploader,
// dashboard, console, and the sketch's setup()) can be compiled unchanged and exercised, with faults injected,
// without a board. It runs the firmware's LOGIC: ordering, locking, recovery, retries, what is deleted and when.
// It says nothing about real timing, the real card's behaviour, the real radio or the real microphone.
//
// What it does check beyond "it runs":
//   * every access to the card is inside the firmware's lock (two threads inside the card at once is counted),
//   * no file handle is used after the card was unmounted,
//   * no more files are open at once than the card library allows,
//   * a power cut loses whatever was not flushed, as a real card would,
//   * Wi-Fi joins only an access point that exists, by its exact name and password, after a delay, and reports why a
//     try failed through the same disconnect event as the real core; WiFi.status() changes only on those events (it is
//     NOT reset by begin()), as in arduino-esp32 3.3.12, so code that trusts a stale status is caught.
//
// Time is virtual: millis() runs `scale` times faster than the wall clock, so a 10-minute recording and a
// 5-minute retry back-off take seconds.

#pragma once

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#include <io.h>
#endif

namespace sim {

namespace fs = std::filesystem;

// ---- Virtual time ---------------------------------------------------------------------------------------------

inline double scale = 100.0;  // virtual milliseconds per real millisecond
inline const std::chrono::steady_clock::time_point t0 = std::chrono::steady_clock::now();

inline uint64_t virtual_ms() {
  const double real = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
  return (uint64_t)(real * scale);
}

// Sleeps for `ms` of virtual time. Waits shorter than a millisecond of real time are spun, because the
// operating system's sleep is too coarse for them.
inline void sleep_virtual(uint64_t ms) {
  const double real_ms = (double)ms / scale;
  const auto until = std::chrono::steady_clock::now() + std::chrono::duration<double, std::milli>(real_ms);
  if (real_ms >= 2.0) {
    std::this_thread::sleep_for(std::chrono::duration<double, std::milli>(real_ms - 1.0));
  }
  while (std::chrono::steady_clock::now() < until) std::this_thread::yield();
}

// ---- The wall clock and the network ---------------------------------------------------------------------------

inline std::atomic<bool> wifi_available{true};  // can the access point be reached?
inline std::atomic<bool> wifi_connected{false};  // set by the simulated Wi-Fi once it has joined an access point
inline std::atomic<bool> ntp_requested{false};
inline std::atomic<bool> ntp_works{true};       // does the network answer time requests?
inline std::atomic<bool> clock_valid{false};    // has the device's clock been set?
inline std::atomic<int64_t> true_epoch_at_boot{1767225600};  // 2026-01-01T00:00:00Z

inline int64_t true_now() { return true_epoch_at_boot + (int64_t)(virtual_ms() / 1000); }

// What time() answers: the real time once the clock is set, seconds since start (so, 1970) before.
inline time_t sim_time(time_t* out) {
  if (!clock_valid && ntp_requested && ntp_works && wifi_available && wifi_connected) clock_valid = true;
  const time_t value = clock_valid ? (time_t)true_now() : (time_t)(virtual_ms() / 1000);
  if (out) *out = value;
  return value;
}

// ---- Tasks that ended themselves -------------------------------------------------------------------------------

struct TaskExit {};

// ---- A deterministic "voice" so every sample of a recording can be checked ------------------------------------

inline int16_t sample_at(uint64_t index) {
  uint32_t h = (uint32_t)index * 0x9E3779B1u;
  h ^= h >> 15;
  h *= 0x85EBCA6Bu;
  h ^= h >> 13;
  return (int16_t)((int32_t)(h & 0x7FFF) - 0x4000);
}

// ---- Memory card ------------------------------------------------------------------------------------------------

struct Impl;

struct SdState {
  std::mutex m;
  fs::path root;
  std::atomic<bool> mounted{false};
  std::atomic<int> begin_failures{0};         // the next N SD.begin() calls fail
  std::atomic<int> zero_writes{0};            // the next N write() calls return 0
  std::atomic<bool> need_remount{false};      // every write returns 0 until the card is remounted
  std::atomic<bool> lose_unflushed{false};    // unmounting drops whatever was not flushed
  std::atomic<int64_t> capacity{-1};          // bytes; negative = plenty
  std::atomic<int64_t> total_written{0};
  std::atomic<int64_t> power_cut_after{-1};   // the power goes after this many bytes were written
  std::atomic<int64_t> eject_after{-1};       // the card disappears after this many bytes
  std::atomic<int64_t> glitch_after{-1};      // writes start failing (until a remount) after this many bytes
  std::atomic<uint32_t> slow_ms{0};           // a slow card: each block write, flush, close and rename takes this long
  std::atomic<bool> ejected{false};
  std::atomic<uint32_t> generation{0};
  std::atomic<int> open_files{0};
  std::atomic<int> max_files{5};
  std::atomic<int> violations{0};
  std::atomic<int> inside{0};
  std::atomic<bool> break_locks{false};       // make the firmware's lock do nothing (to prove the check works)
  std::set<Impl*> registry;
  std::vector<std::string> violation_notes;
};
inline SdState sd;

inline void note_violation(const std::string& what) {
  std::lock_guard<std::mutex> lock(sd.m);
  sd.violations++;
  if (sd.violation_notes.size() < 20) sd.violation_notes.push_back(what);
}

// A slow card: writing, flushing, closing and renaming take this long (simulated time). Reads stay quick.
inline void card_delay() {
  if (sd.slow_ms > 0) sleep_virtual(sd.slow_ms);
}

// Counts two threads inside the card at once. The short spin widens the window so a missing lock shows up.
struct Enter {
  Enter() {
    if (sd.inside.fetch_add(1) != 0) note_violation("two tasks used the memory card at the same time");
    const auto until = std::chrono::steady_clock::now() + std::chrono::microseconds(150);
    while (std::chrono::steady_clock::now() < until) std::this_thread::yield();
  }
  ~Enter() { sd.inside.fetch_sub(1); }
};

inline fs::path real_path(const std::string& p) {
  std::string rel = p;
  while (!rel.empty() && (rel[0] == '/' || rel[0] == '\\')) rel.erase(0, 1);
  return sd.root / rel;
}

struct Impl {
  FILE* f = nullptr;
  fs::path path;
  std::string name;
  bool dir = false;
  bool writable = false;
  bool stale = false;
  bool closed = false;
  int64_t flushed = 0;
  uint32_t gen = 0;
  std::vector<std::string> entries;
  size_t next = 0;
};

inline int64_t file_length(FILE* f) {
  const long cur = ftell(f);
  fseek(f, 0, SEEK_END);
  const long end = ftell(f);
  fseek(f, cur, SEEK_SET);
  return end;
}

// The power goes: every open file keeps only what had been flushed.
[[noreturn]] inline void power_cut() {
  {
    std::lock_guard<std::mutex> lock(sd.m);
    for (Impl* impl : sd.registry) {
      if (impl->f != nullptr) {
        fclose(impl->f);
        impl->f = nullptr;
        std::error_code ignored;
        if (impl->writable && fs::exists(impl->path, ignored)) {
          const auto size = (int64_t)fs::file_size(impl->path, ignored);
          if (size > impl->flushed) fs::resize_file(impl->path, (uintmax_t)impl->flushed, ignored);
        }
      }
    }
  }
  printf("[SIM] POWER CUT\n");
  fflush(stdout);
  _exit(3);
}

// ---- Strings and printing -----------------------------------------------------------------------------------------

}  // namespace sim

namespace fs = std::filesystem;

class String {
 public:
  String() {}
  String(const char* c) : s_(c ? c : "") {}
  String(const std::string& s) : s_(s) {}
  String(char c) : s_(1, c) {}
  String(int v) : s_(std::to_string(v)) {}
  String(unsigned int v) : s_(std::to_string(v)) {}
  String(long v) : s_(std::to_string(v)) {}
  String(unsigned long v) : s_(std::to_string(v)) {}
  String(long long v) : s_(std::to_string(v)) {}
  String(unsigned long long v) : s_(std::to_string(v)) {}
  size_t length() const { return s_.size(); }
  const char* c_str() const { return s_.c_str(); }
  const std::string& str() const { return s_; }
  void trim() {
    const size_t a = s_.find_first_not_of(" \t\r\n");
    const size_t b = s_.find_last_not_of(" \t\r\n");
    s_ = a == std::string::npos ? "" : s_.substr(a, b - a + 1);
  }
  bool startsWith(const String& x) const { return s_.compare(0, x.s_.size(), x.s_) == 0; }
  bool endsWith(const String& x) const {
    return s_.size() >= x.s_.size() && s_.compare(s_.size() - x.s_.size(), x.s_.size(), x.s_) == 0;
  }
  void remove(unsigned int at) { if (at < s_.size()) s_.erase(at); }
  long toInt() const { return atol(s_.c_str()); }
  char operator[](size_t i) const { return s_[i]; }
  String& operator+=(char c) { s_ += c; return *this; }
  String& operator+=(const char* c) { s_ += c; return *this; }
  String& operator+=(const String& o) { s_ += o.s_; return *this; }
  bool operator==(const String& o) const { return s_ == o.s_; }
  bool operator==(const char* o) const { return s_ == o; }
  bool operator!=(const char* o) const { return s_ != o; }
  bool operator!=(const String& o) const { return s_ != o.s_; }

 private:
  std::string s_;
};
inline String operator+(const String& a, const String& b) { return String(a.str() + b.str()); }
inline String operator+(const char* a, const String& b) { return String(std::string(a) + b.str()); }
inline String operator+(const String& a, const char* b) { return String(a.str() + b); }

#define F(text) text
#define PROGMEM
typedef const char* PGM_P;
#define HIGH 1
#define LOW 0
#define INPUT_PULLUP 2
#define FILE_READ "r"
#define FILE_WRITE "w"

namespace sim {
inline std::mutex print_lock;
inline std::deque<int> serial_in;
inline std::mutex serial_lock;
}  // namespace sim

class HardwareSerial {
 public:
  void begin(unsigned long) {}
  size_t print(const char* s) { return emit(s); }
  size_t print(const String& s) { return emit(s.c_str()); }
  size_t println() { return emit("\n"); }
  size_t println(const char* s) { return emit(s, true); }
  size_t println(const String& s) { return emit(s.c_str(), true); }
  int printf(const char* format, ...) __attribute__((format(printf, 2, 3))) {
    char buffer[1024];
    va_list args;
    va_start(args, format);
    const int n = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    emit(buffer);
    return n;
  }
  int available() {
    std::lock_guard<std::mutex> lock(sim::serial_lock);
    return (int)sim::serial_in.size();
  }
  int read() {
    std::lock_guard<std::mutex> lock(sim::serial_lock);
    if (sim::serial_in.empty()) return -1;
    const int c = sim::serial_in.front();
    sim::serial_in.pop_front();
    return c;
  }

 private:
  size_t emit(const char* s, bool newline = false) {
    std::lock_guard<std::mutex> lock(sim::print_lock);
    fputs(s, stdout);
    if (newline) fputc('\n', stdout);
    fflush(stdout);
    return strlen(s);
  }
};
inline HardwareSerial Serial;

namespace sim {
inline void type_line(const std::string& text) {
  std::lock_guard<std::mutex> lock(serial_lock);
  for (char c : text) serial_in.push_back((unsigned char)c);
  serial_in.push_back('\n');
}
}  // namespace sim

// ---- Arduino basics ----------------------------------------------------------------------------------------------------

namespace sim {
inline std::atomic<int> button_level{1};  // HIGH: released
inline std::atomic<int> boot_level{1};    // the board's own BOOT button (GPIO0): HIGH is released
inline std::atomic<uint64_t> psram_size{8u * 1024 * 1024};
}  // namespace sim

inline uint32_t millis() { return (uint32_t)sim::virtual_ms(); }
inline void delay(uint32_t ms) { sim::sleep_virtual(ms); }
// The wiring this simulates: one button between D1 (GPIO2) and GND, the board's own BOOT button (GPIO0), nothing else
// on any pin. A pin that was not
// given the internal pull-up floats, and floating reads LOW here, so a sketch that forgot INPUT_PULLUP, or that
// read another pin, would see a button that is permanently down (or never pressed) and fail its tests.
namespace sim {
inline std::atomic<int> pin_mode[64];
constexpr int kButtonPin = 2;
constexpr int kBootPin = 0;
}  // namespace sim
inline void pinMode(int pin, int mode) {
  if (pin >= 0 && pin < 64) sim::pin_mode[pin] = mode;
}
inline int digitalRead(int pin) {
  if (pin < 0 || pin >= 64 || sim::pin_mode[pin] != INPUT_PULLUP) return LOW;  // floating
  if (pin == sim::kBootPin) return (int)sim::boot_level;                         // the board's BOOT button
  return pin == sim::kButtonPin ? (int)sim::button_level : HIGH;                 // pulled up; GPIO2 has the wired button
}
inline uint32_t analogReadMilliVolts(int) { return 0; }
inline void* ps_malloc(size_t n) { return sim::psram_size == 0 ? nullptr : malloc(n); }  // no PSRAM, no memory
inline void configTime(long, int, const char*, const char*) { sim::ntp_requested = true; }

struct EspClass {
  size_t getPsramSize() { return (size_t)sim::psram_size; }
  size_t getFreePsram() { return (size_t)sim::psram_size / 2; }
  uint32_t getFreeHeap() { return 180000; }
  uint64_t getEfuseMac() { return 0x24D7EBA1B2C3ULL; }
  [[noreturn]] void restart() {
    printf("[SIM] restart requested\n");
    fflush(stdout);
    _exit(77);
  }
};
inline EspClass ESP;

inline uint32_t esp_random() {
  static std::atomic<uint32_t> state{(uint32_t)std::chrono::steady_clock::now().time_since_epoch().count() | 1u};
  uint32_t x = state.load();
  x ^= x << 13;
  x ^= x >> 17;
  x ^= x << 5;
  state = x;
  return x;
}

// ---- FreeRTOS ----------------------------------------------------------------------------------------------------------

typedef int BaseType_t;
typedef unsigned int UBaseType_t;
typedef uint32_t TickType_t;
typedef void* TaskHandle_t;
typedef void (*TaskFunction_t)(void*);
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY 0xFFFFFFFFu
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))

struct SimQueue {
  std::mutex m;
  std::condition_variable cv;
  std::deque<std::vector<uint8_t>> items;
  size_t capacity = 0;
  size_t item_size = 0;
};
typedef SimQueue* QueueHandle_t;

struct SimMutex {
  std::recursive_timed_mutex m;
};
typedef SimMutex* SemaphoreHandle_t;

struct portMUX_TYPE {
  std::mutex m;
};
#define portMUX_INITIALIZER_UNLOCKED {}
inline void portENTER_CRITICAL(portMUX_TYPE* mux) { mux->m.lock(); }
inline void portEXIT_CRITICAL(portMUX_TYPE* mux) { mux->m.unlock(); }

inline void vTaskDelay(TickType_t ticks) { sim::sleep_virtual(ticks); }
// The real call answers in bytes on the ESP32; the simulator has no stack to measure, so it reports a fixed amount.
inline UBaseType_t uxTaskGetStackHighWaterMark(TaskHandle_t) { return 4096; }
inline void taskYIELD() { std::this_thread::yield(); }

inline BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char*, uint32_t, void* arg, int, TaskHandle_t* handle,
                                          int) {
  std::thread([fn, arg]() {
    try {
      fn(arg);
    } catch (const sim::TaskExit&) {
    }
  }).detach();
  if (handle) *handle = (void*)1;
  return pdPASS;
}
[[noreturn]] inline void vTaskDelete(TaskHandle_t) { throw sim::TaskExit{}; }

inline QueueHandle_t xQueueCreate(uint32_t length, uint32_t item_size) {
  SimQueue* q = new SimQueue();
  q->capacity = length;
  q->item_size = item_size;
  return q;
}
inline void vQueueDelete(QueueHandle_t q) { delete q; }
inline BaseType_t xQueueReset(QueueHandle_t q) {
  std::lock_guard<std::mutex> lock(q->m);
  q->items.clear();
  return pdPASS;
}
inline UBaseType_t uxQueueMessagesWaiting(QueueHandle_t q) {
  std::lock_guard<std::mutex> lock(q->m);
  return (UBaseType_t)q->items.size();
}
inline BaseType_t xQueueSend(QueueHandle_t q, const void* item, TickType_t wait) {
  std::unique_lock<std::mutex> lock(q->m);
  auto room = [&]() { return q->items.size() < q->capacity; };
  if (!room()) {
    if (wait == 0) return pdFALSE;
    if (wait == portMAX_DELAY) q->cv.wait(lock, room);
    else if (!q->cv.wait_for(lock, std::chrono::duration<double, std::milli>((double)wait / sim::scale), room)) return pdFALSE;
  }
  q->items.emplace_back((const uint8_t*)item, (const uint8_t*)item + q->item_size);
  q->cv.notify_all();
  return pdTRUE;
}
inline BaseType_t xQueueReceive(QueueHandle_t q, void* item, TickType_t wait) {
  std::unique_lock<std::mutex> lock(q->m);
  auto some = [&]() { return !q->items.empty(); };
  if (!some()) {
    if (wait == 0) return pdFALSE;
    if (wait == portMAX_DELAY) q->cv.wait(lock, some);
    else if (!q->cv.wait_for(lock, std::chrono::duration<double, std::milli>((double)wait / sim::scale), some)) return pdFALSE;
  }
  memcpy(item, q->items.front().data(), q->item_size);
  q->items.pop_front();
  q->cv.notify_all();
  return pdTRUE;
}

inline SemaphoreHandle_t xSemaphoreCreateMutex() { return new SimMutex(); }
inline SemaphoreHandle_t xSemaphoreCreateRecursiveMutex() { return new SimMutex(); }
inline BaseType_t xSemaphoreTake(SemaphoreHandle_t h, TickType_t wait) {
  if (sim::sd.break_locks) return pdTRUE;
  if (wait == portMAX_DELAY) {
    h->m.lock();
    return pdTRUE;
  }
  return h->m.try_lock_for(std::chrono::duration<double, std::milli>((double)wait / sim::scale)) ? pdTRUE : pdFALSE;
}
inline BaseType_t xSemaphoreGive(SemaphoreHandle_t h) {
  if (sim::sd.break_locks) return pdTRUE;
  h->m.unlock();
  return pdTRUE;
}
inline BaseType_t xSemaphoreTakeRecursive(SemaphoreHandle_t h, TickType_t wait) { return xSemaphoreTake(h, wait); }
inline BaseType_t xSemaphoreGiveRecursive(SemaphoreHandle_t h) { return xSemaphoreGive(h); }

// ---- SPI and the card ------------------------------------------------------------------------------------------------------

class SPIClass {
 public:
  void begin(int8_t = -1, int8_t = -1, int8_t = -1, int8_t = -1) {}
  void end() {}
};
inline SPIClass SPI;

class Stream {
 public:
  virtual ~Stream() {}
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  virtual size_t write(uint8_t) = 0;
  virtual size_t readBytes(char* buffer, size_t length) {
    size_t n = 0;
    while (n < length) {
      const int c = read();
      if (c < 0) break;
      buffer[n++] = (char)c;
    }
    return n;
  }
  size_t readBytes(uint8_t* buffer, size_t length) { return readBytes((char*)buffer, length); }
};

class File : public Stream {
 public:
  File() {}
  explicit File(std::shared_ptr<sim::Impl> p) : p_(std::move(p)) {}

  operator bool() const { return p_ && !p_->closed && (p_->f != nullptr || p_->dir); }
  const char* name() const { return p_ ? p_->name.c_str() : ""; }
  bool isDirectory() const { return p_ && p_->dir; }

  size_t write(const uint8_t* buffer, size_t length) {
    if (!usable()) return 0;
    sim::Enter in;
    if (sim::sd.ejected || sim::sd.need_remount) return 0;
    if (length >= 512 && sim::sd.zero_writes > 0) {  // block writes only, not the 44-byte headers
      sim::sd.zero_writes--;
      return 0;
    }
    if (sim::sd.capacity >= 0) {
      const int64_t room = sim::sd.capacity - used_locked();
      if (room < (int64_t)length) length = room > 0 ? (size_t)room : 0;
      if (length == 0) return 0;
    }
    if (length >= 512) sim::card_delay();
    const size_t n = fwrite(buffer, 1, length, p_->f);
    sim::sd.total_written += (int64_t)n;
    if (sim::sd.eject_after >= 0 && sim::sd.total_written >= sim::sd.eject_after) sim::sd.ejected = true;
    if (sim::sd.glitch_after >= 0 && sim::sd.total_written >= sim::sd.glitch_after) {
      sim::sd.glitch_after = -1;
      sim::sd.need_remount = true;
    }
    if (sim::sd.power_cut_after >= 0 && sim::sd.total_written >= sim::sd.power_cut_after) sim::power_cut();
    return n;
  }
  size_t write(uint8_t b) override { return write(&b, 1); }
  size_t read(uint8_t* buffer, size_t length) {
    if (!usable()) return 0;
    sim::Enter in;
    if (sim::sd.ejected) return 0;
    return fread(buffer, 1, length, p_->f);
  }
  int read() override {
    uint8_t b;
    return read(&b, 1) == 1 ? b : -1;
  }
  int peek() override { return -1; }
  int available() override {
    if (!usable()) return 0;
    sim::Enter in;
    return (int)(sim::file_length(p_->f) - ftell(p_->f));
  }
  size_t readBytes(char* buffer, size_t length) override { return read((uint8_t*)buffer, length); }
  using Stream::readBytes;
  bool seek(uint32_t position, int = 0) {
    if (!usable()) return false;
    sim::Enter in;
    return fseek(p_->f, (long)position, SEEK_SET) == 0;
  }
  size_t position() {
    if (!usable()) return 0;
    sim::Enter in;
    return (size_t)ftell(p_->f);
  }
  size_t size() {
    if (!p_) return 0;
    if (p_->dir) return 0;
    if (!usable()) return 0;
    sim::Enter in;
    return (size_t)sim::file_length(p_->f);
  }
  void flush() {
    if (!usable()) return;
    sim::Enter in;
    if (sim::sd.need_remount || sim::sd.ejected) return;  // a card that has stopped answering cannot flush
    if (p_->writable) sim::card_delay();
    fflush(p_->f);
    p_->flushed = sim::file_length(p_->f);
  }
  void close() {
    if (!p_ || p_->closed) return;
    if (p_->stale) {
      sim::note_violation("a file handle from before the card was unmounted was closed");
      p_->closed = true;
      return;
    }
    sim::Enter in;
    if (p_->writable) sim::card_delay();
    std::lock_guard<std::mutex> lock(sim::sd.m);
    if (p_->f != nullptr) {
      const bool failing = sim::sd.need_remount || sim::sd.ejected;
      if (!failing) {
        fflush(p_->f);
        p_->flushed = sim::file_length(p_->f);
      }
      fclose(p_->f);
      p_->f = nullptr;
      std::error_code ignored;
      // Closing a file on a card that has stopped answering does not make its buffered data durable.
      if (failing && sim::sd.lose_unflushed && p_->writable && fs::exists(p_->path, ignored) &&
          (int64_t)fs::file_size(p_->path, ignored) > p_->flushed) {
        fs::resize_file(p_->path, (uintmax_t)p_->flushed, ignored);
      }
    }
    p_->closed = true;
    sim::sd.registry.erase(p_.get());
    sim::sd.open_files--;
  }
  String readString() {
    std::string text;
    uint8_t b;
    while (read(&b, 1) == 1) text += (char)b;
    return String(text);
  }
  File openNextFile() {
    if (!p_ || !p_->dir) return File();
    sim::Enter in;
    if (p_->next >= p_->entries.size()) return File();
    const std::string entry = p_->entries[p_->next++];
    const fs::path full = p_->path / entry;
    auto impl = std::make_shared<sim::Impl>();
    impl->path = full;
    impl->name = entry;
    impl->gen = sim::sd.generation;
    std::error_code ignored;
    if (fs::is_directory(full, ignored)) {
      impl->dir = true;
    } else {
      impl->f = fopen(full.string().c_str(), "rb");
    }
    std::lock_guard<std::mutex> lock(sim::sd.m);
    sim::sd.registry.insert(impl.get());
    sim::sd.open_files++;
    if (sim::sd.open_files > sim::sd.max_files) {
      sim::sd.violations++;
      if (sim::sd.violation_notes.size() < 20) sim::sd.violation_notes.push_back("more files open than the card library allows");
    }
    return File(impl);
  }

 private:
  bool usable() {
    if (!p_ || p_->closed || p_->f == nullptr) return false;
    if (p_->stale || p_->gen != sim::sd.generation) {
      sim::note_violation("a file handle from before the card was unmounted was used");
      return false;
    }
    return true;
  }
  static int64_t used_locked() {
    int64_t total = 0;
    std::error_code ignored;
    for (auto it = fs::recursive_directory_iterator(sim::sd.root, ignored); it != fs::recursive_directory_iterator(); it.increment(ignored)) {
      if (it->is_regular_file(ignored)) total += (int64_t)it->file_size(ignored);
    }
    return total;
  }
  std::shared_ptr<sim::Impl> p_;
};

class SDClass {
 public:
  bool begin(uint8_t, SPIClass&, uint32_t, const char* = "/sd", uint8_t max_files = 5, bool = false) {
    sim::sd.max_files = max_files;
    if (sim::sd.begin_failures > 0) {
      sim::sd.begin_failures--;
      return false;
    }
    if (sim::sd.ejected) return false;
    std::error_code ignored;
    fs::create_directories(sim::sd.root, ignored);
    sim::sd.need_remount = false;
    sim::sd.generation++;
    sim::sd.mounted = true;
    return true;
  }
  void end() {
    std::lock_guard<std::mutex> lock(sim::sd.m);
    for (sim::Impl* impl : sim::sd.registry) {
      if (impl->f != nullptr) {
        // Whatever the firmware still holds open is dead from here on. Unflushed data is lost if asked.
        fclose(impl->f);
        impl->f = nullptr;
        std::error_code ignored;
        if (sim::sd.lose_unflushed && impl->writable && fs::exists(impl->path, ignored)) {
          if ((int64_t)fs::file_size(impl->path, ignored) > impl->flushed) {
            fs::resize_file(impl->path, (uintmax_t)impl->flushed, ignored);
          }
        }
        impl->stale = true;
      }
    }
    sim::sd.registry.clear();
    sim::sd.open_files = 0;
    sim::sd.mounted = false;
  }
  bool exists(const char* path) {
    sim::Enter in;
    if (!sim::sd.mounted || sim::sd.ejected) return false;
    std::error_code ignored;
    return fs::exists(sim::real_path(path), ignored);
  }
  bool exists(const String& path) { return exists(path.c_str()); }
  bool mkdir(const char* path) {
    sim::Enter in;
    if (!sim::sd.mounted || sim::sd.ejected) return false;
    std::error_code ignored;
    return fs::create_directories(sim::real_path(path), ignored);
  }
  bool remove(const char* path) {
    sim::Enter in;
    if (!sim::sd.mounted || sim::sd.ejected) return false;
    std::error_code ignored;
    return fs::remove(sim::real_path(path), ignored);
  }
  bool remove(const String& path) { return remove(path.c_str()); }
  bool rename(const char* from, const char* to) {
    sim::Enter in;
    sim::card_delay();
    if (!sim::sd.mounted || sim::sd.ejected) return false;
    std::error_code ec;
    fs::rename(sim::real_path(from), sim::real_path(to), ec);
    return !ec;
  }
  bool rename(const String& from, const String& to) { return rename(from.c_str(), to.c_str()); }
  File open(const char* path, const char* mode = FILE_READ) {
    sim::Enter in;
    if (!sim::sd.mounted || sim::sd.ejected) return File();
    const fs::path full = sim::real_path(path);
    std::error_code ignored;
    auto impl = std::make_shared<sim::Impl>();
    impl->path = full;
    impl->name = full.filename().string();
    impl->gen = sim::sd.generation;
    if (fs::is_directory(full, ignored)) {
      impl->dir = true;
      for (auto& entry : fs::directory_iterator(full, ignored)) impl->entries.push_back(entry.path().filename().string());
      std::sort(impl->entries.begin(), impl->entries.end());
    } else {
      const std::string m = mode;
      const char* c_mode = m == "w" ? "wb" : (m == "r+" ? "r+b" : (m == "a" ? "ab" : "rb"));
      impl->writable = m != "r";
      impl->f = fopen(full.string().c_str(), c_mode);
      if (impl->f == nullptr) return File();
      impl->flushed = (m == "w") ? 0 : sim::file_length(impl->f);
    }
    std::lock_guard<std::mutex> lock(sim::sd.m);
    sim::sd.registry.insert(impl.get());
    sim::sd.open_files++;
    if (sim::sd.open_files > sim::sd.max_files) {
      sim::sd.violations++;
      if (sim::sd.violation_notes.size() < 20) sim::sd.violation_notes.push_back("more files open than the card library allows");
    }
    return File(impl);
  }
  File open(const String& path, const char* mode = FILE_READ) { return open(path.c_str(), mode); }
  uint64_t totalBytes() {
    sim::Enter in;
    return sim::sd.capacity >= 0 ? (uint64_t)sim::sd.capacity : 16ull * 1024 * 1024 * 1024;
  }
  uint64_t usedBytes() {
    sim::Enter in;
    int64_t total = 0;
    std::error_code ignored;
    for (auto it = fs::recursive_directory_iterator(sim::sd.root, ignored); it != fs::recursive_directory_iterator(); it.increment(ignored)) {
      if (it->is_regular_file(ignored)) total += (int64_t)it->file_size(ignored);
    }
    return (uint64_t)total;
  }
};
inline SDClass SD;

// ---- Settings storage ---------------------------------------------------------------------------------------------------------

namespace sim {
inline fs::path nvs_path;
inline std::map<std::string, std::string> nvs;
inline std::mutex nvs_lock;

inline std::string escape(const std::string& v) {
  std::string out;
  for (char c : v) {
    if (c == '\\') out += "\\\\";
    else if (c == '\n') out += "\\n";
    else if (c == '\t') out += "\\t";
    else out += c;
  }
  return out;
}
inline std::string unescape(const std::string& v) {
  std::string out;
  for (size_t i = 0; i < v.size(); i++) {
    if (v[i] == '\\' && i + 1 < v.size()) {
      i++;
      out += v[i] == 'n' ? '\n' : (v[i] == 't' ? '\t' : v[i]);
    } else {
      out += v[i];
    }
  }
  return out;
}
inline void nvs_load() {
  std::lock_guard<std::mutex> lock(nvs_lock);
  nvs.clear();
  FILE* f = fopen(nvs_path.string().c_str(), "rb");
  if (!f) return;
  char line[2048];
  while (fgets(line, sizeof(line), f)) {
    std::string text = line;
    while (!text.empty() && (text.back() == '\n' || text.back() == '\r')) text.pop_back();
    const size_t tab = text.find('\t');
    if (tab != std::string::npos) nvs[text.substr(0, tab)] = unescape(text.substr(tab + 1));
  }
  fclose(f);
}
inline void nvs_save() {
  FILE* f = fopen(nvs_path.string().c_str(), "wb");
  if (!f) return;
  for (auto& kv : nvs) fprintf(f, "%s\t%s\n", kv.first.c_str(), escape(kv.second).c_str());
  fclose(f);
}
}  // namespace sim

class Preferences {
 public:
  bool begin(const char*, bool) {
    sim::nvs_load();
    return true;
  }
  uint32_t getUInt(const char* key, uint32_t fallback) {
    std::lock_guard<std::mutex> lock(sim::nvs_lock);
    auto it = sim::nvs.find(key);
    return it == sim::nvs.end() ? fallback : (uint32_t)strtoul(it->second.c_str(), nullptr, 10);
  }
  size_t putUInt(const char* key, uint32_t value) {
    std::lock_guard<std::mutex> lock(sim::nvs_lock);
    sim::nvs[key] = std::to_string(value);
    sim::nvs_save();
    return 4;
  }
  String getString(const char* key, const String& fallback) {
    std::lock_guard<std::mutex> lock(sim::nvs_lock);
    auto it = sim::nvs.find(key);
    return it == sim::nvs.end() ? fallback : String(it->second);
  }
  size_t putString(const char* key, const String& value) {
    std::lock_guard<std::mutex> lock(sim::nvs_lock);
    sim::nvs[key] = value.str();
    sim::nvs_save();
    return value.length();
  }
  bool remove(const char* key) {
    std::lock_guard<std::mutex> lock(sim::nvs_lock);
    sim::nvs.erase(key);
    sim::nvs_save();
    return true;
  }
};

// ---- Microphone ---------------------------------------------------------------------------------------------------------------------

namespace sim {
struct MicState {
  std::atomic<bool> fail_begin{false};
  std::atomic<int64_t> stop_after_bytes{-1};  // after this many bytes the microphone goes quiet
  std::atomic<bool> running{false};
  std::atomic<uint64_t> produced{0};
  std::atomic<uint64_t> started_ms{0};
  std::atomic<uint64_t> total_samples_sent{0};
};
inline MicState mic;
}  // namespace sim

enum { I2S_MODE_PDM_RX = 1, I2S_DATA_BIT_WIDTH_16BIT = 16, I2S_SLOT_MODE_MONO = 1 };

class I2SClass {
 public:
  void setPinsPdmRx(int, int) {}
  bool begin(int, uint32_t, int, int) {
    if (sim::mic.fail_begin) return false;
    sim::mic.running = true;
    sim::mic.produced = 0;
    sim::mic.started_ms = sim::virtual_ms();
    return true;
  }
  void end() { sim::mic.running = false; }
  // Hands out samples at the real audio rate (32 bytes per virtual millisecond), never ahead of the clock.
  size_t readBytes(char* buffer, size_t length) {
    if (!sim::mic.running) return 0;
    const uint64_t elapsed = sim::virtual_ms() - sim::mic.started_ms;
    uint64_t allowed = elapsed * 32 - sim::mic.produced;
    if (sim::mic.stop_after_bytes >= 0) {
      const uint64_t cap = (uint64_t)sim::mic.stop_after_bytes;
      allowed = sim::mic.produced >= cap ? 0 : std::min<uint64_t>(allowed, cap - sim::mic.produced);
    }
    if (allowed == 0) {
      sim::sleep_virtual(2);
      return 0;
    }
    size_t n = std::min<uint64_t>(length, allowed) & ~(size_t)1;
    for (size_t i = 0; i < n; i += 2) {
      const int16_t v = sim::sample_at(sim::mic.produced / 2 + i / 2);
      memcpy(buffer + i, &v, 2);
    }
    sim::mic.produced += n;
    return n;
  }
};

// ---- Wi-Fi and HTTP -----------------------------------------------------------------------------------------------------------------

typedef enum { WIFI_MODE_NULL = 0, WIFI_MODE_STA = 1, WIFI_MODE_AP = 2, WIFI_MODE_APSTA = 3 } wifi_mode_t;
#define WIFI_OFF WIFI_MODE_NULL
#define WIFI_STA WIFI_MODE_STA
#define WIFI_AP WIFI_MODE_AP
#define WIFI_AP_STA WIFI_MODE_APSTA
enum { WL_IDLE_STATUS = 0, WL_NO_SSID_AVAIL = 1, WL_CONNECTED = 3, WL_CONNECT_FAILED = 4, WL_CONNECTION_LOST = 5, WL_DISCONNECTED = 6 };
typedef int wl_status_t;
typedef int wifi_err_reason_t;
enum arduino_event_id_t { ARDUINO_EVENT_WIFI_STA_GOT_IP = 1, ARDUINO_EVENT_WIFI_STA_DISCONNECTED = 2, ARDUINO_EVENT_MAX = 99 };
struct SimStaDisconnected {
  uint8_t reason;
};
union arduino_event_info_t {
  SimStaDisconnected wifi_sta_disconnected;
};
using SimWifiCallback = std::function<void(arduino_event_id_t, arduino_event_info_t)>;

class IPAddress {
 public:
  IPAddress() {}
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) : v_{a, b, c, d} {}
  bool operator==(const IPAddress& o) const { return memcmp(v_, o.v_, 4) == 0; }
  String toString() const {
    char text[20];
    snprintf(text, sizeof(text), "%d.%d.%d.%d", v_[0], v_[1], v_[2], v_[3]);
    return String(text);
  }

 private:
  uint8_t v_[4] = {0, 0, 0, 0};
};

namespace sim {
inline std::atomic<int> wifi_mode{WIFI_MODE_NULL};
inline std::atomic<bool> ap_up{false};
inline std::atomic<bool> request_from_ap{true};  // for the dashboard: which network did the request arrive on?

// The access points that exist, with their passwords. A locket can join one only by its exact name and password.
// (The built-in network of the shipped firmware is among them: "ESPTEST" with its password.)
inline std::map<std::string, std::string> access_points = {
    {"ESPTEST", "12345678"},           {"simulated-network", "simulated-password"}, {"simnet", "simulated-password"},
    {"othernet", "otherpass123"},      {"homenet", "topsecretpass"}};
inline std::atomic<uint32_t> ap_delay_ms{600};  // how long joining takes before it succeeds or fails
inline std::mutex wifi_lock;
inline std::string wifi_target_ssid, wifi_target_password, wifi_joined_ssid;
inline std::atomic<int> wifi_phase{0};  // 0 idle, 1 joining
inline std::atomic<uint64_t> wifi_begin_ms{0};
inline std::atomic<int> wifi_status_code{WL_DISCONNECTED};
inline std::atomic<int> wifi_begin_calls{0};
inline std::vector<SimWifiCallback> wifi_callbacks;

// The chip says why a try failed or a link dropped, through the same event the real Wi-Fi library delivers.
inline void wifi_fire_disconnect(int reason) {
  arduino_event_info_t info;
  memset(&info, 0, sizeof(info));
  info.wifi_sta_disconnected.reason = (uint8_t)reason;
  for (auto& callback : wifi_callbacks) callback(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, info);
}

// Joining takes `ap_delay_ms`, then succeeds if the name and password match an access point that is reachable.
inline void wifi_evaluate() {
  std::lock_guard<std::mutex> lock(wifi_lock);
  if (wifi_connected) {
    if (!wifi_available) {  // the access point went away
      wifi_connected = false;
      wifi_status_code = WL_CONNECTION_LOST;
      wifi_fire_disconnect(200);  // BEACON_TIMEOUT
    }
    return;
  }
  if (wifi_phase == 1 && virtual_ms() - wifi_begin_ms >= ap_delay_ms) {
    wifi_phase = 0;
    auto found = access_points.find(wifi_target_ssid);
    if (!wifi_available || found == access_points.end()) {
      wifi_status_code = WL_NO_SSID_AVAIL;
      wifi_fire_disconnect(201);  // NO_AP_FOUND
    } else if (found->second != wifi_target_password) {
      wifi_status_code = WL_CONNECT_FAILED;
      wifi_fire_disconnect(202);  // AUTH_FAIL
    } else {
      wifi_connected = true;
      wifi_status_code = WL_CONNECTED;
      wifi_joined_ssid = wifi_target_ssid;
    }
  }
}
}  // namespace sim

class WiFiClass {
 public:
  int status() {
    sim::wifi_evaluate();
    return sim::wifi_status_code;
  }
  wifi_mode_t getMode() { return (wifi_mode_t)(int)sim::wifi_mode; }
  void mode(wifi_mode_t m) { sim::wifi_mode = (int)m; }
  void setSleep(bool) {}
  void setAutoReconnect(bool) {}
  void persistent(bool) {}
  // Like the real core, status() changes only when the chip reports something (an event). begin() does NOT reset it,
  // so after a failure it keeps saying "failed" until the next outcome: code that trusts it between tries is wrong.
  void begin(const char* ssid, const char* password) {
    std::lock_guard<std::mutex> lock(sim::wifi_lock);
    if (sim::wifi_connected) {  // starting another join drops the link, and the chip says it left
      sim::wifi_connected = false;
      sim::wifi_status_code = WL_DISCONNECTED;
      sim::wifi_fire_disconnect(8);  // ASSOC_LEAVE
    }
    sim::wifi_target_ssid = ssid ? ssid : "";
    sim::wifi_target_password = password ? password : "";
    sim::wifi_phase = 1;
    sim::wifi_begin_ms = sim::virtual_ms();
    sim::wifi_begin_calls++;
  }
  bool disconnect(bool = false, bool = false, unsigned long = 100) {
    std::lock_guard<std::mutex> lock(sim::wifi_lock);
    if (sim::wifi_connected) {
      sim::wifi_connected = false;
      sim::wifi_status_code = WL_DISCONNECTED;
      sim::wifi_fire_disconnect(8);  // ASSOC_LEAVE: the chip reports a leave we asked for
    }
    sim::wifi_phase = 0;  // a join still in progress is abandoned (status and reason are left as they were)
    return true;
  }
  String SSID() const { return sim::wifi_connected ? String(sim::wifi_joined_ssid) : String(); }
  IPAddress localIP() { return IPAddress(192, 168, 43, 57); }
  int8_t RSSI() { return -52; }
  int onEvent(SimWifiCallback callback, arduino_event_id_t = ARDUINO_EVENT_MAX) {
    std::lock_guard<std::mutex> lock(sim::wifi_lock);
    sim::wifi_callbacks.push_back(callback);
    return (int)sim::wifi_callbacks.size();
  }
  const char* disconnectReasonName(wifi_err_reason_t reason) {
    switch (reason) {
      case 1: return "UNSPECIFIED";
      case 8: return "ASSOC_LEAVE";
      case 200: return "BEACON_TIMEOUT";
      case 201: return "NO_AP_FOUND";
      case 202: return "AUTH_FAIL";
      default: return "UNKNOWN";
    }
  }
  bool softAPConfig(IPAddress, IPAddress, IPAddress) { return true; }
  bool softAP(const char*, const char*) {
    sim::ap_up = true;
    return true;
  }
  IPAddress softAPIP() { return IPAddress(192, 168, 4, 1); }
};
inline WiFiClass WiFi;

class WiFiClient {
 public:
  virtual ~WiFiClient() {}
  virtual bool secure() const { return false; }
  IPAddress localIP() { return sim::request_from_ap ? IPAddress(192, 168, 4, 1) : IPAddress(192, 168, 1, 50); }
  size_t write(const uint8_t*, size_t n) { return n; }
};
class WiFiClientSecure : public WiFiClient {
 public:
  bool secure() const override { return true; }
  void setInsecure() { insecure = true; }
  void setCACert(const char* pem) { ca_length = pem ? strlen(pem) : 0; }
  bool insecure = false;
  size_t ca_length = 0;
};

namespace sim {
struct HttpRequest {
  std::string method;
  std::string url;
  std::string path;
  bool tls = false;
  bool insecure = false;
  size_t ca_length = 0;
  std::map<std::string, std::string> headers;  // names in lower case
  std::vector<uint8_t> body;
};
struct HttpResponse {
  int status = 200;
  std::map<std::string, std::string> headers;
  std::string body;
  bool chunked = false;       // no Content-Length: the client cannot know the size
  int transport_error = 0;    // negative: no answer at all
};
inline std::function<HttpResponse(const HttpRequest&)> server;
inline std::mutex server_lock;
}  // namespace sim

enum {
  HTTPC_ERROR_CONNECTION_REFUSED = -1,
  HTTPC_ERROR_SEND_PAYLOAD_FAILED = -3,
  HTTPC_ERROR_READ_TIMEOUT = -11,
};

class HTTPClient {
 public:
  bool begin(WiFiClient& client, const String& url) {
    req_ = sim::HttpRequest();
    req_.url = url.str();
    req_.tls = url.startsWith("https://");
    if (const WiFiClientSecure* s = dynamic_cast<WiFiClientSecure*>(&client)) {
      req_.insecure = s->insecure;
      req_.ca_length = s->ca_length;
    }
    const size_t scheme = req_.url.find("://");
    const size_t slash = req_.url.find('/', scheme == std::string::npos ? 0 : scheme + 3);
    req_.path = slash == std::string::npos ? "/" : req_.url.substr(slash);
    sent_headers_.clear();
    collect_.clear();
    resp_ = sim::HttpResponse();
    return true;
  }
  void setTimeout(uint32_t) {}
  void addHeader(const String& name, const String& value) {
    std::string key = name.str();
    std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) { return (char)tolower(c); });
    req_.headers[key] = value.str();
  }
  void collectHeaders(const char** names, size_t count) {
    for (size_t i = 0; i < count; i++) {
      std::string key = names[i];
      std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) { return (char)tolower(c); });
      collect_.insert(key);
    }
  }
  int POST(const String& body) {
    req_.method = "POST";
    req_.body.assign(body.str().begin(), body.str().end());
    req_.headers["content-length"] = std::to_string(req_.body.size());
    return finish();
  }
  int sendRequest(const char* type, Stream* stream, size_t size) {
    if (!stream) return -1;
    req_.method = type;
    if (size > 0) req_.headers["content-length"] = std::to_string(size);
    // The real client's loop: ask how much is available, read at most 1460 bytes, send them.
    size_t left = size;
    uint8_t buffer[1460];
    while (stream->available() > -1 && left > 0) {
      int available = stream->available();
      if (available == 0) {
        sim::sleep_virtual(1);
        continue;
      }
      size_t want = std::min<size_t>({(size_t)available, left, sizeof(buffer)});
      const size_t got = stream->readBytes(buffer, want);
      req_.body.insert(req_.body.end(), buffer, buffer + got);
      left -= got;
    }
    if (left > 0) return HTTPC_ERROR_READ_TIMEOUT;  // the body never finished; the server would wait, then give up
    return finish();
  }
  int getSize() { return resp_.chunked ? -1 : (int)resp_.body.size(); }
  String getString() { return String(resp_.body); }
  String header(const char* name) {
    std::string key = name;
    std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) { return (char)tolower(c); });
    if (!collect_.count(key)) return String();
    auto it = resp_.headers.find(key);
    return it == resp_.headers.end() ? String() : String(it->second);
  }
  void end() {}

 private:
  int finish() {
    if (!(sim::wifi_connected && sim::wifi_available)) return HTTPC_ERROR_CONNECTION_REFUSED;
    std::lock_guard<std::mutex> lock(sim::server_lock);
    resp_ = sim::server ? sim::server(req_) : sim::HttpResponse{};
    for (auto& kv : resp_.headers) {
      std::string key = kv.first;
      std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) { return (char)tolower(c); });
      if (key != kv.first) {
        resp_.headers[key] = kv.second;
      }
    }
    if (resp_.transport_error < 0) return resp_.transport_error;
    return resp_.status;
  }
  sim::HttpRequest req_;
  std::map<std::string, std::string> sent_headers_;
  std::set<std::string> collect_;
  sim::HttpResponse resp_;
};

// ---- The local dashboard's web server -----------------------------------------------------------------------------------------------

enum HTTPMethod { HTTP_ANY = 0, HTTP_GET = 1, HTTP_POST = 3 };

namespace sim {
struct WebReply {
  int status = 0;
  std::string type;
  std::string body;
};
inline std::map<std::string, std::function<void()>> web_routes;
inline WebReply web_reply;
inline WiFiClient web_client;
inline std::map<std::string, std::string> web_args;
}  // namespace sim

class WebServer {
 public:
  explicit WebServer(int) {}
  typedef std::function<void()> THandlerFunction;
  void on(const char* uri, HTTPMethod, THandlerFunction handler) { sim::web_routes[uri] = handler; }
  void on(const char* uri, THandlerFunction handler) { sim::web_routes[uri] = handler; }
  void onNotFound(THandlerFunction) {}
  void begin() {}
  void handleClient() {}
  void sendHeader(const char*, const char*) {}
  void send(int code, const char* type, const String& body) { reply(code, type, body.str()); }
  void send(int code, const char* type, const char* body) { reply(code, type, body); }
  void send_P(int code, PGM_P type, PGM_P body) { reply(code, type, body); }
  void setContentLength(size_t) {}
  String arg(const char* name) { return String(sim::web_args[name]); }
  WiFiClient& client() { return sim::web_client; }

 private:
  void reply(int code, const char* type, const std::string& body) {
    sim::web_reply.status = code;
    sim::web_reply.type = type;
    sim::web_reply.body = body;
  }
};

// Time: from here on, firmware code calling time() gets the simulated clock. Everything that needs the real one
// is above this line.
#define time(out) sim_time_hook(out)
inline time_t sim_time_hook(time_t* out) { return sim::sim_time(out); }
