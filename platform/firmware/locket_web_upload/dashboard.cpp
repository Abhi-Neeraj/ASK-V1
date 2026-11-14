#include "dashboard.h"

#include <WebServer.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "config.h"
#include "core.h"
#include "wifi_manager.h"
#include "recorder.h"
#include "sdwriter.h"
#include "settings.h"
#include "status.h"
#include "storage.h"
#include "uploader.h"

namespace dashboard {

static WebServer server(80);

static const IPAddress kApAddress(192, 168, 4, 1);
static const IPAddress kApGateway(192, 168, 4, 1);
static const IPAddress kApMask(255, 255, 255, 0);

const char kPage[] PROGMEM = R"rawliteral(<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Locket maintenance</title>
<style>
body{margin:0;background:#0a0a0a;color:#fff;font-family:Arial,sans-serif;padding:20px}
.wrap{max-width:760px;margin:auto}.card{border:1px solid #2a3a46;border-radius:14px;padding:22px;background:#10171d}
.eyebrow{color:#2fa8d6;font-size:12px;font-weight:700;letter-spacing:.16em}h1{font-size:30px;margin:6px 0 4px}
.sub{color:#9db3c3;margin-bottom:18px;font-size:14px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.panel{background:#18232c;border-radius:10px;padding:14px}.label{color:#8fb0c6;font-size:12px;margin-bottom:4px;letter-spacing:.08em}
.value{font-size:18px;font-weight:700;word-break:break-word}.bad{color:#ff8da6}.ok{color:#8ae0aa}.small{font-size:13px;color:#9db3c3;margin-top:14px}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style></head><body><div class="wrap"><div class="card">
<div class="eyebrow">LOCKET MAINTENANCE</div><h1>Voice locket</h1>
<div class="sub">Press the button to start a recording, press it again to stop. Recordings are sent to the server.</div>
<div class="panel"><div class="label">STATE</div><div id="state" class="value">...</div>
<div id="error" class="value bad" style="font-size:14px;margin-top:6px"></div></div>
<div class="grid" style="margin-top:10px">
<div class="panel"><div class="label">RECORDING</div><div id="rec" class="value">-</div></div>
<div class="panel"><div class="label">AUDIO CAPTURE</div><div id="mic" class="value">-</div></div>
<div class="panel"><div class="label">MEMORY CARD</div><div id="card" class="value">-</div></div>
<div class="panel"><div class="label">CARD WRITER</div><div id="writer" class="value">-</div></div>
<div class="panel"><div class="label">WI-FI</div><div id="wifi" class="value">-</div></div>
<div class="panel"><div class="label">SERVER</div><div id="server" class="value">-</div></div>
<div class="panel"><div class="label">WAITING TO UPLOAD</div><div id="queue" class="value">-</div></div>
<div class="panel"><div class="label">LAST UPLOAD</div><div id="last" class="value">-</div></div>
</div>
<div class="panel" style="margin-top:10px"><div class="label">LAST PROBLEM</div><div id="problem" class="value" style="font-size:14px">-</div></div>
<div class="small" id="foot"></div>
</div></div>
<script>
function ago(s){return s<0?'never':(s<90?s+' s ago':Math.round(s/60)+' min ago')}
function put(id,t,bad){var e=document.getElementById(id);e.textContent=t;e.className='value'+(bad?' bad':'')}
function refresh(){fetch('/status',{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){
put('state',s.state,s.state==='ERROR');put('error',s.error||'');
put('rec',s.recording.seconds+' s, part '+s.recording.part);
put('mic',s.microphone==='ok'?'OK (peak '+s.recording.micPeak+')':s.microphone,s.microphone==='failed');
put('card',s.card.ready?'OK, '+s.card.freeMB+' MB free':'NOT READY',!s.card.ready);
put('writer',s.card.writeRetries+' retries, '+s.card.remounts+' remounts, queue '+s.card.queueHighWater+'/'+s.card.ringBlocks+(s.card.bytesLost?', LOST '+s.card.bytesLost+' B':''),s.card.bytesLost>0);
put('wifi',s.wifi.connected?s.wifi.ssid+', '+s.wifi.ip+' ('+s.wifi.rssi+' dBm)':'not connected to '+s.wifi.ssid+(s.wifi.reason?': '+s.wifi.reason:''),!s.wifi.connected);
put('server',!s.server.paired?'not paired':(s.server.needsPairing?'pair again':(s.server.reachable?'online':'not reachable')),!s.server.paired||s.server.needsPairing||!s.server.reachable);
put('queue',(s.uploads.uploading?'UPLOADING - ':'')+s.uploads.waiting+' waiting, '+s.uploads.setAside+' set aside, '+s.uploads.untimed+' untimed',s.uploads.setAside>0||s.uploads.untimed>0);
put('last',ago(s.uploads.lastUploadAgoSeconds)+' ('+s.uploads.uploaded+' sent since start)');
put('problem',s.problem||'none');
document.getElementById('foot').textContent='Firmware '+s.firmware+' - clock '+(s.clockSet?'set':'not set')+' - up '+s.uptimeSeconds+' s - heap '+s.freeHeap+' B';
}).catch(function(){})}
refresh();setInterval(refresh,2000);
</script></body></html>)rawliteral";

// Only requests that arrived on the locket's own network are answered. The page is for whoever is standing next to
// the locket with the network's password, not for other devices on the Wi-Fi the locket uses to reach the server.
static bool ownNetworkOnly() {
  if (server.client().localIP() == WiFi.softAPIP()) return true;
  server.send(403, "text/plain", "Use the locket's own Wi-Fi network");
  return false;
}

static void sendStatus() {
  if (!ownNetworkOnly()) return;
  const uploader::Status net = uploader::status();
  char error[96], errorEscaped[200], problem[200], recordingId[40];
  status::error(error, sizeof(error));
  if (!ask::json_escape(error, errorEscaped, sizeof(errorEscaped))) errorEscaped[0] = '\0';
  if (!ask::json_escape(net.lastProblem, problem, sizeof(problem))) problem[0] = '\0';
  if (!ask::json_escape(net.lastRecordingId, recordingId, sizeof(recordingId))) recordingId[0] = '\0';
  // The network's name, address and last failure reason: never its password, which is not available here.
  const wifi_manager::Status wifi = wifi_manager::status();
  char wifiName[80], wifiReason[220];
  if (!ask::json_escape(wifi.ssid, wifiName, sizeof(wifiName))) wifiName[0] = '\0';
  if (!ask::json_escape(wifi.lastReason, wifiReason, sizeof(wifiReason))) wifiReason[0] = '\0';

  const recorder::Mic mic = recorder::microphone();
  const char* micText = mic == recorder::Mic::Ok ? "ok" : (mic == recorder::Mic::Failed ? "failed" : "unknown");

  char json[2100];
  snprintf(json, sizeof(json),
           "{\"firmware\":\"%s\",\"state\":\"%s\",\"error\":\"%s\",\"clockSet\":%s,\"uptimeSeconds\":%lu,"
           "\"freeHeap\":%lu,\"microphone\":\"%s\","
           "\"recording\":{\"seconds\":%lu,\"part\":%lu,\"micPeak\":%lu,\"sessions\":%lu,\"partsSaved\":%lu,"
           "\"tailsDropped\":%lu},"
           "\"card\":{\"ready\":%s,\"freeMB\":%lu,\"writeRetries\":%lu,\"remounts\":%lu,\"queueHighWater\":%lu,"
           "\"ringBlocks\":%lu,\"bytesLost\":%lu},"
           "\"wifi\":{\"connected\":%s,\"rssi\":%d,\"ssid\":\"%s\",\"ip\":\"%s\",\"builtIn\":%s,\"reason\":\"%s\"},"
           "\"server\":{\"paired\":%s,\"needsPairing\":%s,\"reachable\":%s,\"heartbeatAgoSeconds\":%ld},"
           "\"uploads\":{\"uploading\":%s,\"waiting\":%lu,\"setAside\":%lu,\"untimed\":%lu,\"uploaded\":%lu,"
           "\"lastUploadAgoSeconds\":%ld,\"lastRecordingId\":\"%s\"},\"problem\":\"%s\"}",
           ASK_FIRMWARE_VERSION, status::stateName(status::state()), errorEscaped, net.clockSet ? "true" : "false",
           (unsigned long)(millis() / 1000), (unsigned long)ESP.getFreeHeap(), micText,
           (unsigned long)status::counters.sessionSeconds, (unsigned long)status::counters.sessionPart,
           (unsigned long)status::counters.micPeak, (unsigned long)status::counters.sessions,
           (unsigned long)status::counters.partsSaved, (unsigned long)status::counters.tailsDropped,
           storage::ready() ? "true" : "false", (unsigned long)(storage::freeBytes() / (1024 * 1024)),
           (unsigned long)status::counters.sdWriteRetries, (unsigned long)status::counters.sdRemounts,
           (unsigned long)status::counters.sdQueueHighWater, (unsigned long)kRingBlockCount,
           (unsigned long)status::counters.bytesLost, net.wifiConnected ? "true" : "false", net.rssi, wifiName, wifi.ip,
           wifi.builtIn ? "true" : "false", wifiReason, net.paired ? "true" : "false", net.needsPairing ? "true" : "false", net.serverReachable ? "true" : "false",
           (long)net.lastHeartbeatAgoSeconds, net.uploading ? "true" : "false", (unsigned long)storage::pendingCount(),
           (unsigned long)storage::rejectedCount(), (unsigned long)storage::untimedCount(), (unsigned long)net.uploaded,
           (long)net.lastUploadAgoSeconds, recordingId, problem);
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", json);
}

static void sendPage() {
  if (!ownNetworkOnly()) return;
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "text/html", kPage);
}

#if ASK_DASHBOARD_AUDIO_DOWNLOAD
// Support use only: lists and downloads recordings still on the card. Off in the shipped firmware.
static void sendQueueList() {
  if (!ownNetworkOnly()) return;
  char name[ask::kClipNameBytes];
  if (!storage::oldest(kDirPending, name, sizeof(name))) {
    server.send(200, "text/plain", "nothing waiting");
    return;
  }
  server.send(200, "text/plain", String("oldest waiting: ") + name + "\nGET /clip?name=<file> to download it");
}

static void sendClip() {
  if (!ownNetworkOnly()) return;
  if (status::busyRecording()) {
    server.send(409, "text/plain", "Not while recording");
    return;
  }
  ask::ClipName parsed;
  const String name = server.arg("name");
  if (!ask::clip_name_parse(name.c_str(), &parsed)) {
    server.send(400, "text/plain", "Not a recording name");
    return;
  }
  storage::Reader reader;
  if (!reader.open(kDirPending, name.c_str())) {
    server.send(404, "text/plain", "Not found");
    return;
  }
  server.setContentLength((size_t)reader.size());
  server.send(200, "audio/wav", "");
  static uint8_t slice[2048];
  while (reader.position() < reader.size()) {
    const size_t got = reader.read(slice, sizeof(slice));
    if (got == 0 || server.client().write(slice, got) != got) break;
  }
}
#endif

static void task(void*) {
  for (;;) {
    server.handleClient();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void begin() {
  if (!settings::apEnabled()) {
    Serial.println(F("[DASHBOARD] Off. Type 'ap on' on the USB serial connection to switch the maintenance Wi-Fi on."));
    return;
  }
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAPConfig(kApAddress, kApGateway, kApMask);
  const String ssid = settings::apSsid();
  if (!WiFi.softAP(ssid.c_str(), settings::apPassword().c_str())) {
    Serial.println(F("[DASHBOARD] Could not start the maintenance Wi-Fi."));
    return;
  }
  Serial.printf("[DASHBOARD] Maintenance Wi-Fi \"%s\" at http://%s/ (password: type 'ap' on the serial connection)\n",
                ssid.c_str(), WiFi.softAPIP().toString().c_str());

  server.on("/", HTTP_GET, sendPage);
  server.on("/status", HTTP_GET, sendStatus);
  server.on("/test", HTTP_GET, []() {
    if (ownNetworkOnly()) server.send(200, "text/plain", "OK");
  });
#if ASK_DASHBOARD_AUDIO_DOWNLOAD
  server.on("/queue", HTTP_GET, sendQueueList);
  server.on("/clip", HTTP_GET, sendClip);
  Serial.println(F("WARNING: recordings can be downloaded from the maintenance Wi-Fi (ASK_DASHBOARD_AUDIO_DOWNLOAD)."));
#endif
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
  server.begin();
  xTaskCreatePinnedToCore(task, "dashboard", kDashboardStackBytes, nullptr, 1, nullptr, 0);
}

}  // namespace dashboard
