// Minimal stand-ins for the Arduino and ESP32 libraries the locket sketch uses. They exist ONLY so the sketch's
// own files can be compiled on a computer (`-fsyntax-only`) to catch mistakes between those files: a call to a
// function that does not exist, a wrong argument, a missing include.
//
// They were written from memory of the real libraries, so they prove nothing about the real libraries. A
// successful compile against these is NOT a successful build for the board.

#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <string>

#define F(text) text
#define FILE_READ "r"
#define FILE_WRITE "w"
#define pdMS_TO_TICKS(ms) (ms)

typedef void (*TaskFunction_t)(void*);
inline void vTaskDelay(uint32_t) {}
inline int xTaskCreatePinnedToCore(TaskFunction_t, const char*, uint32_t, void*, int, void*, int) { return 1; }
inline uint32_t millis() { return 0; }
inline void delay(uint32_t) {}
inline uint32_t analogReadMilliVolts(int) { return 0; }
// esp_random() is NOT declared here: in the real core it lives in <esp_random.h>, which Arduino.h does not include
// (checked against arduino-esp32 3.3.12 and ESP-IDF 5.5). A sketch must include it itself; see stubs/esp_random.h.
struct EspClass {
  void restart() {}
};
static EspClass ESP;

class String {
 public:
  String() {}
  String(const char* c) : s_(c ? c : "") {}
  String(char c) : s_(1, c) {}
  String(int v) : s_(std::to_string(v)) {}
  String(unsigned v) : s_(std::to_string(v)) {}
  String(long v) : s_(std::to_string(v)) {}
  String(unsigned long v) : s_(std::to_string(v)) {}
  String(long long v) : s_(std::to_string(v)) {}
  size_t length() const { return s_.size(); }
  const char* c_str() const { return s_.c_str(); }
  int indexOf(char c, unsigned from = 0) const {
    const size_t at = s_.find(c, from);
    return at == std::string::npos ? -1 : (int)at;
  }
  String substring(unsigned from) const { return String(s_.substr(from).c_str()); }
  String substring(unsigned from, unsigned to) const { return String(s_.substr(from, to - from).c_str()); }
  bool endsWith(const String& x) const { return s_.size() >= x.s_.size() && s_.compare(s_.size() - x.s_.size(), x.s_.size(), x.s_) == 0; }
  bool startsWith(const String& x) const { return s_.compare(0, x.s_.size(), x.s_) == 0; }
  void trim() {}
  void remove(unsigned at) { s_.erase(at); }
  long toInt() const { return atol(s_.c_str()); }
  char operator[](size_t i) const { return s_[i]; }
  String& operator+=(char c) { s_ += c; return *this; }
  String& operator+=(const String& o) { s_ += o.s_; return *this; }
  String operator+(const String& o) const { String r(*this); r.s_ += o.s_; return r; }
  bool operator==(const String& o) const { return s_ == o.s_; }
  bool operator==(const char* o) const { return s_ == o; }
  bool operator!=(const char* o) const { return s_ != o; }
  bool operator<(const String& o) const { return s_ < o.s_; }

 private:
  std::string s_;
};
inline String operator+(const char* a, const String& b) { return String(a) + b; }

class HardwareSerial {
 public:
  void begin(unsigned long) {}
  void println() {}
  void println(const String&) {}
  void println(const char*) {}
  template <typename... A>
  void printf(const char*, A...) {}
  int available() { return 0; }
  int read() { return -1; }
};
static HardwareSerial Serial;

// ---- Preferences.h
class Preferences {
 public:
  bool begin(const char*, bool) { return true; }
  uint32_t getUInt(const char*, uint32_t d) { return d; }
  size_t putUInt(const char*, uint32_t) { return 4; }
  String getString(const char*, const String& d) { return d; }
  size_t putString(const char*, const String&) { return 1; }
  bool remove(const char*) { return true; }
};

// ---- FS.h / SD.h
class Stream {
 public:
  virtual ~Stream() {}
};
class File : public Stream {
 public:
  operator bool() const { return false; }
  bool isDirectory() { return false; }
  File openNextFile() { return File(); }
  const char* name() { return ""; }
  void close() {}
  size_t size() { return 0; }
  size_t read(uint8_t*, size_t) { return 0; }
  size_t write(const uint8_t*, size_t) { return 0; }
  bool seek(size_t) { return true; }
  String readString() { return String(); }
};
class SDClass {
 public:
  bool begin(int) { return true; }
  bool exists(const char*) { return true; }
  bool mkdir(const char*) { return true; }
  File open(const char*, const char* = FILE_READ) { return File(); }
  File open(const String&, const char* = FILE_READ) { return File(); }
  bool rename(const String&, const String&) { return true; }
  bool remove(const String&) { return true; }
  uint64_t totalBytes() { return 0; }
  uint64_t usedBytes() { return 0; }
};
static SDClass SD;

// ---- WiFi.h / WiFiClientSecure.h / HTTPClient.h
enum { WL_CONNECTED = 3 };
enum { WIFI_STA = 1 };
class WiFiClass {
 public:
  int status() { return 0; }
  void mode(int) {}
  void begin(const char*, const char*) {}
};
static WiFiClass WiFi;
inline void configTime(long, int, const char*, const char*) {}

class WiFiClient {
 public:
  virtual ~WiFiClient() {}
};
class WiFiClientSecure : public WiFiClient {
 public:
  void setInsecure() {}
  void setCACert(const char*) {}
};
class HTTPClient {
 public:
  bool begin(WiFiClient&, const String&) { return true; }
  void setTimeout(uint32_t) {}
  void addHeader(const String&, const String&) {}
  void collectHeaders(const char**, size_t) {}
  int POST(const String&) { return 0; }
  int sendRequest(const char*, Stream*, size_t) { return 0; }
  int getSize() { return 0; }
  String getString() { return String(); }
  String header(const char*) { return String(); }
  void end() {}
};

// ---- ESP_I2S.h
enum { I2S_MODE_PDM_RX = 1, I2S_DATA_BIT_WIDTH_16BIT = 16, I2S_SLOT_MODE_MONO = 1 };
class I2SClass {
 public:
  void setPinsPdmRx(int, int) {}
  bool begin(int, uint32_t, int, int) { return true; }
  size_t readBytes(char*, size_t) { return 0; }
};
