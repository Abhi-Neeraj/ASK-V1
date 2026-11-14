// Runs the real locket firmware (the sketch's own setup(), and every module it uses) against SIMULATED hardware
// and a fake or real server. See hostsim.h for what is and is not simulated, and tests/README.md.
//
//   hostsim --dir <world> [options] <action> [<action> ...]
//
// <world> is a folder standing in for the locket: <world>/sd is the memory card, <world>/nvs.txt its settings
// storage, <world>/server what the fake server was sent. Running the program again on the same folder is a reboot.
// The last thing printed is one line, RESULT {json}, describing what is on the card, what the server holds and
// what the firmware counted.

#include <Arduino.h>

#include "../../locket_web_upload/locket_web_upload.ino"
#include "hostsim_http.h"

namespace {

// ---- The fake server ----------------------------------------------------------------------------------------------

struct Logged {
  std::string method, path, note;
  int status = 0;
  bool tls = false, insecure = false;
  size_t ca = 0;
};

struct Stored {
  std::string id, sha, session, recorded_at, uuid;
  long sequence = -1;
  size_t bytes = 0;
};

struct World {
  std::mutex m;
  std::string mode = "ok";
  int counter = 0;
  std::map<std::string, Stored> stored;
  std::vector<Logged> log;
  std::string credential = "sim-credential-0123456789abcdef";
  fs::path dir;
  int uuids = 0;
  int bad_requests = 0;
  uint32_t max_clip = 600;
  uint32_t max_upload = 26214400;
  std::set<std::string> conflicted;
  bool forward = false;
} world;

const std::string kBase = "/api/device/v1/";

std::string esc(const std::string& s) {
  std::string out;
  for (char c : s) {
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if (c == '\n') {
      out += "\\n";
    } else if ((unsigned char)c < 0x20) {
      out += ' ';
    } else {
      out += c;
    }
  }
  return out;
}

sim::HttpResponse respond(int status, const std::string& body, const char* type = "application/json") {
  sim::HttpResponse r;
  r.status = status;
  r.body = body;
  r.headers["content-type"] = type;
  return r;
}

sim::HttpResponse error_reply(int status, const char* code) {
  return respond(status, std::string("{\"error\":{\"code\":\"") + code + "\",\"message\":\"simulated\"}}");
}

std::string config_json() {
  char text[200];
  snprintf(text, sizeof(text),
           "{\"chunkSeconds\":10,\"heartbeatIntervalSeconds\":30,\"sampleRates\":[16000],\"maxUploadBytes\":%u,"
           "\"maxClipSeconds\":%u}",
           world.max_upload, world.max_clip);
  return text;
}

bool mode_is(const char* name) { return world.mode == name; }
int mode_count(const char* prefix) {
  const std::string p = std::string(prefix) + ":";
  return world.mode.compare(0, p.size(), p) == 0 ? atoi(world.mode.c_str() + p.size()) : -1;
}

std::string sha_hex(const std::vector<uint8_t>& body) {
  ask::Sha256 h;
  ask::sha256_begin(&h);
  ask::sha256_update(&h, body.data(), body.size());
  uint8_t digest[32];
  char hex[65];
  ask::sha256_finish(&h, digest);
  ask::hex_encode(digest, 32, hex);
  return hex;
}

sim::HttpResponse put_recording(const sim::HttpRequest& r, std::string* note) {
  const std::string id = r.path.substr((kBase + "recordings/").size());
  if (mode_is("denied403")) return error_reply(403, "forbidden");
  if (mode_is("too-large413")) return error_reply(413, "upload_too_large");
  if (mode_is("invalid422")) return error_reply(422, "invalid_audio");
  if (mode_is("html200")) return respond(200, "<html><body>Sign in to the guest Wi-Fi</body></html>", "text/html");
  const int errors = mode_count("error500");
  if (errors >= 0 && world.counter < errors) {
    world.counter++;
    return error_reply(500, "internal_error");
  }
  // The very first id the server sees is claimed by a different clip. A locket that gives the clip a new id gets in.
  if (mode_is("conflict-first") && (world.conflicted.empty() || world.conflicted.count(id))) {
    world.conflicted.insert(id);
    return error_reply(409, "recording_conflict");
  }

  // The same checks the real server makes. A request that fails them is a firmware bug, counted separately.
  auto header = [&](const char* name) {
    auto it = r.headers.find(name);
    return it == r.headers.end() ? std::string() : it->second;
  };
  const std::string sha = sha_hex(r.body);
  std::string complaint;
  if (r.tls && (r.insecure || r.ca_length == 0)) complaint = "https without a certificate check";
  else if (header("content-type") != "audio/wav") complaint = "content-type is not audio/wav";
  else if (header("content-length") != std::to_string(r.body.size())) complaint = "content-length does not match the body";
  else if (header("x-content-sha256") != sha) complaint = "sha-256 header does not match the body";
  else if (header("x-recorded-at").size() != 20 || header("x-recorded-at")[10] != 'T' || header("x-recorded-at")[19] != 'Z')
    complaint = "x-recorded-at is not a UTC timestamp";
  else if (header("x-session-ref").empty()) complaint = "x-session-ref is missing";
  else if (header("x-sequence").empty() || header("x-sequence").find_first_not_of("0123456789") != std::string::npos)
    complaint = "x-sequence is not a number";
  uint32_t audio_bytes = 0;
  if (complaint.empty() && (r.body.size() < ask::kWavHeaderBytes ||
                            ask::wav_check(r.body.data(), r.body.size(), 16000, 1, 16, &audio_bytes) != ask::WavCheck::Ok)) {
    complaint = "the body is not a valid WAV";
  }
  if (!complaint.empty()) {
    world.bad_requests++;
    *note = complaint;
    return error_reply(422, "validation_error");
  }

  bool duplicate = false;
  Stored* record;
  auto existing = world.stored.find(id);
  if (existing != world.stored.end()) {
    if (existing->second.sha != sha) return error_reply(409, "recording_conflict");
    duplicate = true;
    record = &existing->second;
  } else {
    Stored s;
    s.id = id;
    s.sha = sha;
    s.session = header("x-session-ref");
    s.sequence = atol(header("x-sequence").c_str());
    s.recorded_at = header("x-recorded-at");
    s.bytes = r.body.size();
    char uuid[40];
    snprintf(uuid, sizeof(uuid), "%08x-0000-4000-8000-%012x", 0xA5C0DE00u + (unsigned)world.uuids, (unsigned)world.uuids);
    world.uuids++;
    s.uuid = uuid;
    fs::create_directories(world.dir / "server");
    FILE* f = fopen((world.dir / "server" / (id + ".wav")).string().c_str(), "wb");
    if (f) {
      fwrite(r.body.data(), 1, r.body.size(), f);
      fclose(f);
    }
    record = &(world.stored[id] = s);
  }

  const int lost = mode_count("lost-ack");
  if (lost >= 0 && world.counter < lost) {
    world.counter++;
    sim::HttpResponse r2;
    r2.transport_error = -11;  // stored, but the answer never arrived
    *note = "answer lost";
    return r2;
  }

  std::string sha_in_ack = record->sha;
  if (mode_is("wrong-ack")) sha_in_ack[0] = sha_in_ack[0] == '0' ? '1' : '0';
  char ack[700];
  snprintf(ack, sizeof(ack),
           "{\"recordingId\":\"%s\",\"clientRecordingId\":\"%s\",\"status\":\"stored\",\"sha256\":\"%s\",\"bytes\":%lu,"
           "\"durationMs\":%lu,\"duplicate\":%s,\"startedAt\":\"%s\"}",
           record->uuid.c_str(), record->id.c_str(), sha_in_ack.c_str(), (unsigned long)record->bytes,
           (unsigned long)((record->bytes - ask::kWavHeaderBytes) / 32), duplicate ? "true" : "false",
           record->recorded_at.c_str());
  sim::HttpResponse out = respond(duplicate ? 200 : 201, ack);
  if (mode_is("chunked-ack")) out.chunked = true;
  return out;
}

sim::HttpResponse route(const sim::HttpRequest& r, std::string* note) {
  if (mode_is("outage")) {
    sim::HttpResponse out;
    out.transport_error = -1;
    return out;
  }
  if (r.path == kBase + "pair") {
    char code[40] = "";
    const std::string body(r.body.begin(), r.body.end());
    ask::json_string(body.c_str(), "code", code, sizeof(code));
    if (mode_is("bad-code") || code[0] == '\0') return error_reply(404, "invalid_pairing_code");
    return respond(200, "{\"deviceId\":\"LKT-SIM-0001\",\"credential\":\"" + world.credential + "\",\"config\":" +
                            config_json() + "}");
  }
  auto auth = r.headers.find("authorization");
  const bool authed = auth != r.headers.end() && auth->second == "Bearer " + world.credential;
  if (mode_is("auth401") || !authed) return error_reply(401, "invalid_device_credential");
  if (r.path == kBase + "heartbeat") return respond(200, "{\"serverTime\":\"2026-01-01T00:00:00Z\",\"config\":" + config_json() + "}");
  if (r.path.compare(0, (kBase + "recordings/").size(), kBase + "recordings/") == 0 && r.method == "PUT") {
    return put_recording(r, note);
  }
  return error_reply(404, "not_found");
}

sim::HttpResponse handle(const sim::HttpRequest& r) {
  std::string note;
  sim::HttpResponse out;
  // A server that takes a very long time to answer. Only the network task waits; nothing else may notice.
  if (world.mode == "slow" && r.method == "PUT") sim::sleep_virtual(40000);
  if (world.forward) {
    out = sim::forward_to_real(r);
    if (r.tls && (r.insecure || r.ca_length == 0)) note = "https without a certificate check";
  } else {
    std::lock_guard<std::mutex> lock(world.m);
    out = route(r, &note);
  }
  std::lock_guard<std::mutex> lock(world.m);
  Logged entry;
  entry.method = r.method;
  entry.path = r.path;
  entry.status = out.transport_error < 0 ? out.transport_error : out.status;
  entry.tls = r.tls;
  entry.insecure = r.insecure;
  entry.ca = r.ca_length;
  entry.note = note;
  world.log.push_back(entry);
  return out;
}

// ---- Driving the device ------------------------------------------------------------------------------------------------

bool wait_until(const std::function<bool()>& done, uint64_t virtual_ms_limit) {
  const uint64_t until = sim::virtual_ms() + virtual_ms_limit;
  while (sim::virtual_ms() < until) {
    if (done()) return true;
    sim::sleep_virtual(5);
  }
  return done();
}

// Holds for at least `virtual_ms` of simulated time AND `real_ms` of real time. The real minimum matters: the
// firmware polls the button from its own thread, and on a busy computer that thread can be kept off the processor
// for a few milliseconds, which at high simulation speeds would let a short press pass unseen.
void hold(uint64_t virtual_ms, int real_ms) {
  const auto real_until = std::chrono::steady_clock::now() + std::chrono::milliseconds(real_ms);
  const uint64_t virtual_until = sim::virtual_ms() + virtual_ms;
  while (std::chrono::steady_clock::now() < real_until || sim::virtual_ms() < virtual_until) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}

void press() {
  // A press with contact bounce, which the firmware's debouncing must turn into exactly one press.
  sim::button_level = 0;
  sim::sleep_virtual(4);  // contact bounce lasts a few milliseconds, always less than the debounce time
  sim::button_level = 1;
  sim::sleep_virtual(4);
  sim::button_level = 0;
  hold(200, 80);
  sim::button_level = 1;
  hold(150, 80);
}

// One clean press and release of the board's own BOOT button (GPIO0).
void boot_press() {
  sim::boot_level = 0;
  hold(200, 80);
  sim::boot_level = 1;
  hold(150, 80);
}

// A worse press than press(): the contact chatters for 20 simulated milliseconds before it settles, and chatters
// again on the way up. The firmware must still see exactly one press.
void bounce_press() {
  for (int i = 0; i < 5; i++) {
    sim::button_level = 0;
    sim::sleep_virtual(2);
    sim::button_level = 1;
    sim::sleep_virtual(2);
  }
  sim::button_level = 0;
  hold(250, 80);
  for (int i = 0; i < 4; i++) {
    sim::button_level = 1;
    sim::sleep_virtual(2);
    sim::button_level = 0;
    sim::sleep_virtual(2);
  }
  sim::button_level = 1;
  hold(150, 80);
}

std::string files_json(const char* dir) {
  std::string out = "[";
  std::error_code ignored;
  std::vector<fs::path> names;
  for (auto& e : fs::directory_iterator(sim::sd.root / dir, ignored)) names.push_back(e.path());
  std::sort(names.begin(), names.end());
  bool first = true;
  for (auto& p : names) {
    if (!first) out += ",";
    first = false;
    out += "{\"name\":\"" + esc(p.filename().string()) + "\",\"size\":" + std::to_string(fs::file_size(p, ignored)) + "}";
  }
  return out + "]";
}

void dump() {
  char error[96], problem[96];
  status::error(error, sizeof(error));
  const uploader::Status net = uploader::status();
  std::string out = "RESULT {";
  out += "\"state\":\"" + std::string(status::stateName(status::state())) + "\",";
  out += "\"error\":\"" + esc(error) + "\",\"problem\":\"" + esc(net.lastProblem) + "\",";
  out += "\"paired\":" + std::string(net.paired ? "true" : "false") + ",";
  out += "\"needsPairing\":" + std::string(net.needsPairing ? "true" : "false") + ",";
  out += "\"uploaded\":" + std::to_string(net.uploaded) + ",";
  out += "\"uploading\":" + std::string(net.uploading ? "true" : "false") + ",";
  out += "\"serverReachable\":" + std::string(net.serverReachable ? "true" : "false") + ",";
  out += "\"micBytes\":" + std::to_string((unsigned long long)sim::mic.produced) + ",";
  {
    const wifi_manager::Status wifi = wifi_manager::status();
    const char* states[] = {"no-network", "connecting", "connected", "waiting"};
    out += "\"wifi\":{\"connected\":" + std::string(wifi.connected ? "true" : "false") + ",\"state\":\"" +
           states[(int)wifi.state] + "\",\"ssid\":\"" + esc(wifi.ssid) + "\",\"builtIn\":" +
           std::string(wifi.builtIn ? "true" : "false") + ",\"ip\":\"" + wifi.ip + "\",\"rssi\":" +
           std::to_string(wifi.rssi) + ",\"attempts\":" + std::to_string(wifi.attempts) + ",\"reasonCode\":" +
           std::to_string(wifi.lastReasonCode) + ",\"reason\":\"" + esc(wifi.lastReason) + "\",\"beginCalls\":" +
           std::to_string((int)sim::wifi_begin_calls) + "},";
  }
  out += "\"counters\":{\"sessions\":" + std::to_string(status::counters.sessions) +
         ",\"partsSaved\":" + std::to_string(status::counters.partsSaved) +
         ",\"tailsDropped\":" + std::to_string(status::counters.tailsDropped) +
         ",\"sdWriteRetries\":" + std::to_string(status::counters.sdWriteRetries) +
         ",\"sdRemounts\":" + std::to_string(status::counters.sdRemounts) +
         ",\"bytesLost\":" + std::to_string(status::counters.bytesLost) +
         ",\"sessionSeconds\":" + std::to_string(status::counters.sessionSeconds) +
         ",\"transitions\":" + std::to_string(status::counters.transitions) +
         ",\"pressesIgnored\":" + std::to_string(status::counters.pressesIgnored) + "},";
  out += "\"dirs\":{\"recording\":" + files_json("recording") + ",\"pending\":" + files_json("pending") +
         ",\"rejected\":" + files_json("rejected") + ",\"untimed\":" + files_json("untimed") + "},";
  {
    std::lock_guard<std::mutex> lock(world.m);
    out += "\"server\":{\"badRequests\":" + std::to_string(world.bad_requests) + ",\"stored\":[";
    bool first = true;
    for (auto& kv : world.stored) {
      if (!first) out += ",";
      first = false;
      const Stored& s = kv.second;
      out += "{\"id\":\"" + s.id + "\",\"sha\":\"" + s.sha + "\",\"bytes\":" + std::to_string(s.bytes) +
             ",\"session\":\"" + s.session + "\",\"sequence\":" + std::to_string(s.sequence) + ",\"recordedAt\":\"" +
             s.recorded_at + "\"}";
    }
    out += "],\"requests\":[";
    first = true;
    for (auto& l : world.log) {
      if (!first) out += ",";
      first = false;
      out += "{\"method\":\"" + l.method + "\",\"path\":\"" + esc(l.path) + "\",\"status\":" + std::to_string(l.status) +
             ",\"tls\":" + (l.tls ? "true" : "false") + ",\"insecure\":" + (l.insecure ? "true" : "false") +
             ",\"ca\":" + std::to_string(l.ca) + ",\"note\":\"" + esc(l.note) + "\"}";
    }
    out += "]},";
  }
  out += "\"violations\":" + std::to_string((int)sim::sd.violations) + ",\"violationNotes\":[";
  {
    std::lock_guard<std::mutex> lock(sim::sd.m);
    for (size_t i = 0; i < sim::sd.violation_notes.size(); i++) {
      if (i) out += ",";
      out += "\"" + esc(sim::sd.violation_notes[i]) + "\"";
    }
  }
  out += "],\"trueNow\":" + std::to_string((long long)sim::true_now());
  out += "}";
  (void)problem;
  printf("%s\n", out.c_str());
  fflush(stdout);
}

void web(const std::string& path, bool from_ap) {
  sim::request_from_ap = from_ap;
  sim::web_reply = sim::WebReply();
  auto it = sim::web_routes.find(path);
  if (it == sim::web_routes.end()) {
    printf("WEB 404 no route\n");
  } else {
    it->second();
    printf("WEB %d %s\n", sim::web_reply.status, sim::web_reply.body.c_str());
  }
  fflush(stdout);
}

void corrupt_oldest_pending() {
  char name[ask::kClipNameBytes];
  if (!storage::oldest(kDirPending, name, sizeof(name))) return;
  const fs::path p = sim::sd.root / "pending" / name;
  FILE* f = fopen(p.string().c_str(), "r+b");
  if (!f) return;
  fputc('X', f);  // the first byte of "RIFF"
  fclose(f);
}

void write_file(const fs::path& p, const std::string& text) {
  fs::create_directories(p.parent_path());
  FILE* f = fopen(p.string().c_str(), "wb");
  if (f) {
    fwrite(text.data(), 1, text.size(), f);
    fclose(f);
  }
}

void run_action(const std::string& action) {
  const size_t eq = action.find('=');
  const std::string name = action.substr(0, eq);
  const std::string value = eq == std::string::npos ? "" : action.substr(eq + 1);
  const long number = atol(value.c_str());

  if (name == "record") {
    press();
    if (!wait_until([] { return status::state() == status::State::Recording; }, 30000)) {
      printf("[SIM] recording did not start (state %s)\n", status::stateName(status::state()));
      return;
    }
    wait_until([&] { return status::counters.sessionSeconds >= (uint32_t)number || status::state() != status::State::Recording; },
               (uint64_t)(number + 120) * 1000);
    if (status::state() == status::State::Recording) press();
    wait_until([] { return status::state() == status::State::Idle || status::state() == status::State::Error; }, 600000);
  } else if (name == "press") {
    press();  // one clean press and release, without waiting for anything
  } else if (name == "boot-press") {
    boot_press();  // the same, on the board's own BOOT button
  } else if (name == "bounce") {
    bounce_press();
  } else if (name == "button-down") {
    sim::button_level = 0;
    hold(200, 60);
  } else if (name == "button-up") {
    sim::button_level = 1;
    hold(200, 60);
  } else if (name == "hold") {
    // A long press: down for `value` simulated seconds, then up. One press, however long it lasts.
    sim::button_level = 0;
    hold((uint64_t)number * 1000, 80);
    sim::button_level = 1;
    hold(150, 80);
  } else if (name == "stop-nowait") {
    press();  // press and return at once, so what happens next happens while the recording is being saved
  } else if (name == "start") {
    press();
    wait_until([] { return status::state() == status::State::Recording; }, 30000);
  } else if (name == "stop") {
    press();
    wait_until([] { return status::state() == status::State::Idle || status::state() == status::State::Error; }, 600000);
  } else if (name == "wait") {
    sim::sleep_virtual((uint64_t)number * 1000);
  } else if (name == "until-empty") {
    wait_until([] { return storage::pendingCount() == 0; }, (uint64_t)number * 1000);
    sim::sleep_virtual(500);
  } else if (name == "clock-on") {
    sim::ntp_works = true;
  } else if (name == "clock-off") {
    sim::ntp_works = false;
  } else if (name == "wifi-up") {
    sim::wifi_available = true;
  } else if (name == "wifi-down") {
    sim::wifi_available = false;
  } else if (name == "ap-set") {  // name:password. Adds the access point, or changes its password.
    std::lock_guard<std::mutex> lock(sim::wifi_lock);
    sim::access_points[value.substr(0, value.find(':'))] = value.substr(value.find(':') + 1);
  } else if (name == "ap-remove") {  // the access point is switched off
    std::lock_guard<std::mutex> lock(sim::wifi_lock);
    sim::access_points.erase(value);
  } else if (name == "server") {
    std::lock_guard<std::mutex> lock(world.m);
    world.mode = value;
    world.counter = 0;
  } else if (name == "type") {
    sim::type_line(value);
    sim::sleep_virtual(4000);
  } else if (name == "web") {
    web(value, true);
  } else if (name == "web-sta") {
    web(value, false);
  } else if (name == "corrupt-oldest") {
    corrupt_oldest_pending();
  } else if (name == "powercut") {
    sim::power_cut();
  } else if (name == "break-locks") {
    sim::sd.break_locks = true;
  } else if (name == "dump") {
    dump();
  } else {
    printf("[SIM] unknown action: %s\n", action.c_str());
    fflush(stdout);
    _exit(64);
  }
}

}  // namespace

int main(int argc, char** argv) {
  fs::path dir = fs::temp_directory_path() / "hostsim";
  std::string url = "https://ask.test";
  std::map<std::string, std::string> prefs;
  bool paired = false, ca = true, forward = false;
  long real_timeout = 180;
  std::vector<std::string> actions;

  for (int i = 1; i < argc; i++) {
    const std::string a = argv[i];
    auto next = [&]() { return i + 1 < argc ? std::string(argv[++i]) : std::string(); };
    if (a == "--dir") dir = next();
    else if (a == "--scale") sim::scale = atof(next().c_str());
    else if (a == "--server") world.mode = next();
    else if (a == "--server-max-clip") world.max_clip = (uint32_t)atol(next().c_str());
    else if (a == "--url") url = next();
    else if (a == "--credential") world.credential = next();
    else if (a == "--paired") paired = true;
    else if (a == "--forward") forward = true;
    else if (a == "--no-ca") ca = false;
    else if (a == "--wifi") sim::wifi_available = next() == "up";
    else if (a == "--ap") {  // an access point that exists: name:password (the first colon splits them)
      const std::string kv = next();
      sim::access_points[kv.substr(0, kv.find(':'))] = kv.substr(kv.find(':') + 1);
    }
    else if (a == "--ap-clear") sim::access_points.clear();
    else if (a == "--ap-delay") sim::ap_delay_ms = (uint32_t)atol(next().c_str());
    else if (a == "--clock") sim::clock_valid = next() == "set";
    else if (a == "--ntp") sim::ntp_works = next() == "works";
    else if (a == "--pref") {
      const std::string kv = next();
      prefs[kv.substr(0, kv.find('='))] = kv.substr(kv.find('=') + 1);
    }
    else if (a == "--hold-button") sim::button_level = 0;  // the button is already down when power comes on
    else if (a == "--sd-slow") sim::sd.slow_ms = (uint32_t)atol(next().c_str());
    else if (a == "--sd-capacity") sim::sd.capacity = atoll(next().c_str());
    else if (a == "--sd-zero-writes") sim::sd.zero_writes = atoi(next().c_str());
    else if (a == "--sd-need-remount") sim::sd.need_remount = true;
    else if (a == "--sd-lose-unflushed") sim::sd.lose_unflushed = true;
    else if (a == "--sd-begin-failures") sim::sd.begin_failures = atoi(next().c_str());
    else if (a == "--power-cut-after") sim::sd.power_cut_after = atoll(next().c_str());
    else if (a == "--eject-after") sim::sd.eject_after = atoll(next().c_str());
    else if (a == "--sd-glitch-after") sim::sd.glitch_after = atoll(next().c_str());
    else if (a == "--max-files") sim::sd.max_files = atoi(next().c_str());
    else if (a == "--mic-stop-after") sim::mic.stop_after_bytes = atoll(next().c_str());
    else if (a == "--mic-fail") sim::mic.fail_begin = true;
    else if (a == "--psram") sim::psram_size = (uint64_t)atoll(next().c_str());
    else if (a == "--timeout") real_timeout = atol(next().c_str());
    else if (a.rfind("--", 0) == 0) {
      printf("[SIM] unknown option: %s\n", a.c_str());
      return 64;
    } else actions.push_back(a);
  }

  world.dir = dir;
  world.forward = forward;
  fs::create_directories(dir / "sd");
  sim::sd.root = dir / "sd";
  sim::nvs_path = dir / "nvs.txt";
  sim::server = handle;
  if (ca) {
    write_file(dir / "sd" / "ca.pem", "-----BEGIN CERTIFICATE-----\nSIMULATED\n-----END CERTIFICATE-----\n");
  } else {
    std::error_code ignored;
    fs::remove(dir / "sd" / "ca.pem", ignored);  // a card from an earlier run may still carry one
  }
  if (paired || !prefs.empty()) {
    sim::nvs_load();
    if (paired) {
      sim::nvs["devid"] = "LKT-SIM-0001";
      sim::nvs["cred"] = world.credential;
      sim::nvs["server"] = url;
    }
    for (auto& kv : prefs) sim::nvs[kv.first] = kv.second;
    sim::nvs_save();
  }

  // If anything hangs, say so and stop instead of waiting for ever.
  std::thread([real_timeout]() {
    std::this_thread::sleep_for(std::chrono::seconds(real_timeout));
    printf("[SIM] TIMED OUT after %ld real seconds\n", real_timeout);
    fflush(stdout);
    _exit(124);
  }).detach();

  setup();  // the sketch's own start-up
  std::thread([]() {
    for (;;) loop();  // the sketch's own loop
  }).detach();

  // A person never presses the button in the first instant after power-up, and the capture task needs a moment to
  // start watching it (a press held before it starts is correctly ignored until released).
  hold(500, 100);

  for (const std::string& action : actions) run_action(action);
  dump();
  fflush(stdout);
  _exit(0);
}
