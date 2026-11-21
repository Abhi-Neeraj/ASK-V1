// Sends finished meetings from the card to the paired phone over Bluetooth. See sender.h and spec 5.3.
//
// After the final review: a meeting recorded before the clock was set waits for the phone's TIME and is renamed
// with its real start (or set aside under /untimed if that can no longer be known, never sent as 1970); both card
// folders are listed under one lock and a failed listing never ends a meeting; the card is read 16 frames at a
// time; and if the phone acknowledges nothing for a while, sending goes back to just after its last ACK (a lost
// RESEND no longer stalls the meeting).
//
// NEVER RUN ON A DEVICE where this was written: the frame, audio, numbering and timing decisions it relies on are
// tested on a computer (ble_core); the card reads, the task and the radio are not.

#include "sender.h"

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>

#include "ble_core.h"
#include "ble_link.h"
#include "config.h"
#include "core.h"
#include "ota.h"
#include "settings.h"
#include "status.h"
#include "storage.h"

namespace sender {

struct Part {
  char name[ask::kClipNameBytes];
  uint32_t samples;
};

struct Meeting {
  bool active;
  char ref[ask::kClientIdBytes];  // the recorder's session reference
  char id[17];                    // its 16-character Bluetooth session ID
  int64_t startMs;                // when the first part began
  uint32_t parts;
  Part part[askble::kMaxParts];
  askble::SessionMap map;
  bool complete;   // no more parts will come
  uint32_t next;   // the next frame to send (0 = START)
  uint32_t endSeq; // the END frame's number once known
  bool endSent;
  uint32_t endSentMs;
  uint32_t acked;         // the phone has every frame up to this one (0 = nothing acknowledged yet)
  uint32_t progressMs;    // when the phone last acknowledged something new or asked for a resend
};

// What the phone said, as queued for the sender task (the full Command is too big to queue many of).
struct Message {
  askble::Command::Kind kind;
  char session[17];
  uint32_t seq;
  uint64_t number;
};

static Meeting meeting;  // only the sender task touches it
static QueueHandle_t commands = nullptr;
static TaskHandle_t task = nullptr;
static volatile uint32_t lastSent = 0;
static char activeId[17] = "";
static uint32_t refused = 0;  // frames the Bluetooth stack would not take since the last stall note (for the log)

static const uint32_t kEndRepeatMs = 10000;  // send END again if the phone has not acknowledged it

void wake() {
  if (task != nullptr) xTaskNotifyGive(task);
}

void onCommand(const askble::Command& c) {
  if (commands == nullptr) return;
  if (c.kind != askble::Command::Ack && c.kind != askble::Command::Resend && c.kind != askble::Command::Time) return;
  Message m;
  m.kind = c.kind;
  memcpy(m.session, c.session, sizeof(m.session));
  m.seq = c.seq;
  m.number = c.number;
  xQueueSend(commands, &m, 0);  // a full queue drops it; a stalled meeting recovers on its own (stall_rewind)
  wake();
}

const char* state() {
  if (ota::running()) return "updating";
  if (status::busyRecording()) return "recording";
  return activeId[0] ? "sending" : "idle";
}

const char* sessionId() { return activeId; }
uint32_t lastSeq() { return lastSent; }

// --- reading the card --------------------------------------------------------------------------------------

// 16 frames of audio read in one go: one open, seek and close per 16 frames instead of per frame.
static const uint32_t kCacheBlocks = 16;
static const size_t kBlockBytes = askble::kAdpcmSamples * 2;
static uint8_t cache[kCacheBlocks * kBlockBytes];
static int32_t cachePart = -1;
static uint32_t cacheFirst = 0;
static size_t cacheBytes = 0;

static void forgetCache() { cachePart = -1; }

// forEachFile takes a plain function, so the listing is collected through these.
static const char* listRef = nullptr;
static Meeting* listInto = nullptr;
static bool listFoundRef = false;
static char listRecordingRef[ask::kClientIdBytes];

static void collectPart(const char* name, uint32_t size) {
  ask::ClipName clip;
  if (!ask::clip_name_parse(name, &clip) || strcmp(clip.session, listRef) != 0) return;
  if (listInto->parts >= askble::kMaxParts || size <= ask::kWavHeaderBytes) return;
  Part& p = listInto->part[listInto->parts++];
  strncpy(p.name, name, sizeof(p.name) - 1);
  p.name[sizeof(p.name) - 1] = '\0';
  p.samples = (size - ask::kWavHeaderBytes) / 2;
}

static void noteRecording(const char* name, uint32_t) {
  ask::ClipName clip;
  if (!ask::clip_name_parse(name, &clip)) return;
  listFoundRef = true;
  strncpy(listRecordingRef, clip.session, sizeof(listRecordingRef) - 1);
}

static int byPart(const void* a, const void* b) {
  ask::ClipName x, y;
  ask::clip_name_parse(((const Part*)a)->name, &x);
  ask::clip_name_parse(((const Part*)b)->name, &y);
  return x.part < y.part ? -1 : (x.part > y.part ? 1 : 0);
}

// (Re)reads the meeting's parts and works out whether more can come. Both folders are listed under one card lock
// (/recording first), so the writer cannot move a part from one to the other in between.
static void scan(Meeting* m) {
  forgetCache();
  static Meeting before;  // kept if the listing fails (static: a Meeting is too big for the task's stack)
  before = *m;
  bool listed = false;
  bool partBeingWritten = false;
  bool otherMeetingRecording = false;
  {
    storage::Lock lock;
    if (lock.held()) {
      listFoundRef = false;
      listRecordingRef[0] = '\0';
      const bool recordingListed = storage::forEachFile(kDirRecording, noteRecording);
      partBeingWritten = listFoundRef && strcmp(listRecordingRef, m->ref) == 0;
      otherMeetingRecording = listFoundRef && !partBeingWritten;
      m->parts = 0;
      listRef = m->ref;
      listInto = m;
      listed = recordingListed && storage::forEachFile(kDirPending, collectPart);
    }
  }
  if (!listed) {
    *m = before;  // a failed listing changes nothing; try again later
    return;
  }
  qsort(m->part, m->parts, sizeof(Part), byPart);
  m->map.parts = m->parts;
  for (uint32_t i = 0; i < m->parts; i++) m->map.blocks[i] = askble::blocks_for_samples(m->part[i].samples);
  m->complete = askble::meeting_complete(true, partBeingWritten, otherMeetingRecording, status::busyRecording());
  if (m->complete && m->endSeq == 0) m->endSeq = askble::session_total_blocks(m->map) + 1;
}

// Moves every listed part of the meeting out of /pending.
static void setAsideMeeting(const Meeting& m, const char* why) {
  for (uint32_t i = 0; i < m.parts; i++) storage::moveFile(kDirPending, m.part[i].name, kDirUntimed);
  Serial.printf("[SEND] %s (%lu part(s) kept under %s)\n", why, (unsigned long)m.parts, kDirUntimed);
}

// Renames each untimed part with the time it really started (same start-up, clock now set). False if any could not.
static bool timeParts(const Meeting& m) {
  const int64_t now = (int64_t)time(nullptr);
  bool all = true;
  for (uint32_t i = 0; i < m.parts; i++) {
    ask::ClipName clip;
    if (!ask::clip_name_parse(m.part[i].name, &clip) || clip.timed) continue;
    int64_t started = 0;
    if (askble::meeting_start_time(false, ask::client_id_boot(clip.client_id), settings::bootCount(), now,
                                   millis() / 1000, clip.when, &started) != askble::StartTime::Resolved) {
      all = false;
      continue;
    }
    clip.timed = true;
    clip.when = started;
    char renamed[ask::kClipNameBytes];
    if (!ask::clip_name_make(renamed, sizeof(renamed), clip) ||
        !storage::renameFile(kDirPending, m.part[i].name, renamed)) {
      all = false;
    }
  }
  return all;
}

// The oldest waiting meeting, ready to send. False if there is none, or it must wait for the phone's TIME.
static bool pickOldest(Meeting* m) {
  char name[ask::kClipNameBytes];
  ask::ClipName clip;
  if (!storage::oldest(kDirPending, name, sizeof(name)) || !ask::clip_name_parse(name, &clip)) return false;
  memset(m, 0, sizeof(*m));
  strncpy(m->ref, clip.session, sizeof(m->ref) - 1);
  askble::session_id_make(m->ref, m->id);
  m->active = true;
  scan(m);
  if (m->parts == 0) return false;

  ask::ClipName first;
  if (!ask::clip_name_parse(m->part[0].name, &first)) return false;
  int64_t started = 0;
  switch (askble::meeting_start_time(first.timed, ask::client_id_boot(first.client_id), settings::bootCount(),
                                     (int64_t)time(nullptr), millis() / 1000, first.when, &started)) {
    case askble::StartTime::Known:
      m->startMs = started * 1000;
      return true;
    case askble::StartTime::Wait:
      return false;  // the phone sends TIME as soon as it connects
    case askble::StartTime::Resolved:
      if (timeParts(*m)) Serial.printf("[SEND] meeting %s: start time worked out now that the clock is set\n", m->id);
      return false;  // pick it again with its new names
    case askble::StartTime::Unknowable:
      setAsideMeeting(*m, "a meeting recorded before the clock was set, in an earlier start-up, cannot be dated");
      return false;
  }
  return false;
}

// --- frames ------------------------------------------------------------------------------------------------

static uint8_t frame[askble::kFrameHeaderBytes + askble::kAdpcmBlockBytes];

static bool sendControl(uint8_t type, uint32_t seq) {
  const size_t n = askble::frame_encode(type, meeting.id, seq, (uint64_t)meeting.startMs, nullptr, 0, frame,
                                        sizeof(frame));
  return n && ble_link::notifyAudio(frame, n);
}

// One block's samples from the card (through the cache). The last block of a part is padded with silence.
static bool readBlock(uint32_t partIndex, uint32_t block, int16_t pcm[askble::kAdpcmSamples]) {
  if (cachePart != (int32_t)partIndex || block < cacheFirst || block >= cacheFirst + kCacheBlocks ||
      (size_t)(block - cacheFirst) * kBlockBytes >= cacheBytes) {
    uint32_t count = meeting.map.blocks[partIndex] - block;
    if (count > kCacheBlocks) count = kCacheBlocks;
    const uint64_t offset = ask::kWavHeaderBytes + (uint64_t)block * kBlockBytes;
    const size_t got = storage::readAt(kDirPending, meeting.part[partIndex].name, offset, cache, count * kBlockBytes);
    if (got == 0) {
      forgetCache();
      return false;  // the card is busy or the file went away: try again shortly
    }
    cachePart = (int32_t)partIndex;
    cacheFirst = block;
    cacheBytes = got;
  }
  const size_t from = (size_t)(block - cacheFirst) * kBlockBytes;
  size_t length = cacheBytes - from;
  if (length > kBlockBytes) length = kBlockBytes;
  memset(pcm, 0, kBlockBytes);
  memcpy(pcm, cache + from, length);
  return true;
}

static bool sendAudio(uint32_t seq) {
  uint32_t partIndex = 0, block = 0;
  if (!askble::session_locate(meeting.map, seq, &partIndex, &block)) return false;
  int16_t pcm[askble::kAdpcmSamples];
  if (!readBlock(partIndex, block, pcm)) return false;
  uint8_t adpcm[askble::kAdpcmBlockBytes];
  askble::adpcm_encode_block_auto(pcm, adpcm);
  const uint64_t ts = (uint64_t)meeting.startMs + (uint64_t)(seq - 1) * 16;
  const size_t n = askble::frame_encode(askble::kFrameAudio, meeting.id, seq, ts, adpcm, sizeof(adpcm), frame,
                                        sizeof(frame));
  return n && ble_link::notifyAudio(frame, n);
}

static void deleteMeeting() {
  forgetCache();
  for (uint32_t i = 0; i < meeting.parts; i++) storage::removeFile(kDirPending, meeting.part[i].name);
  Serial.printf("[SEND] meeting %s delivered to the phone (%lu part(s) removed from the card)\n", meeting.id,
                (unsigned long)meeting.parts);
  meeting.active = false;
  activeId[0] = '\0';
}

// Finds the waiting meeting whose Bluetooth ID the phone named (it may have restarted and lost track).
static bool selectById(const char* id) {
  static Meeting probe;  // too big for the task's stack
  if (!pickOldest(&probe)) return false;
  // Only the oldest meeting is a candidate: meetings are always sent oldest first.
  if (strcmp(probe.id, id) != 0) return false;
  meeting = probe;
  meeting.progressMs = millis();
  strcpy(activeId, meeting.id);
  return true;
}

static void handle(const Message& c) {
  if (c.kind == askble::Command::Time) {
    struct timeval tv;
    tv.tv_sec = (time_t)(c.number / 1000);
    tv.tv_usec = (suseconds_t)((c.number % 1000) * 1000);
    if (ask::clock_is_plausible((int64_t)tv.tv_sec)) settimeofday(&tv, nullptr);
    return;
  }
  if (!meeting.active || strcmp(c.session, meeting.id) != 0) {
    if (c.kind == askble::Command::Resend && selectById(c.session)) meeting.next = c.seq;
    return;
  }
  if (c.kind == askble::Command::Resend) {
    meeting.next = c.seq;
    meeting.endSent = false;
    meeting.progressMs = millis();
  } else if (c.kind == askble::Command::Ack) {
    if (c.seq > meeting.acked) {
      meeting.acked = c.seq;
      meeting.progressMs = millis();
    }
    // The phone says it has everything up to this frame. After a rewind (it went quiet, then its confirmation
    // arrived late) never send again what it already holds: carry on after the frame it confirmed.
    if (!meeting.endSent && c.seq >= meeting.next && c.seq <= askble::session_total_blocks(meeting.map)) {
      meeting.next = c.seq + 1;
    }
    if (meeting.endSent && c.seq >= meeting.endSeq) deleteMeeting();
  }
}

static void run(void*) {
  uint32_t lastScan = 0;
  for (;;) {
    Message c;
    while (xQueueReceive(commands, &c, 0) == pdTRUE) handle(c);

    if (!ble_link::connected() || ota::running() || !storage::ready()) {
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));
      continue;
    }
    // With kSendWhileRecording off (record first, send when done): nothing crosses Bluetooth while the locket is
    // recording; a meeting that was being sent carries on afterwards, from where the phone last confirmed.
    if (!kSendWhileRecording && status::busyRecording()) {
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));
      continue;
    }
    if (!meeting.active) {
      if (!pickOldest(&meeting)) {
        meeting.active = false;
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5000));
        continue;
      }
      strcpy(activeId, meeting.id);
      meeting.progressMs = millis();
      lastScan = millis();
    }
    const uint32_t total = askble::session_total_blocks(meeting.map);

    // Frames are outstanding and the phone has gone quiet: a RESEND may have been lost. Go back to just after the
    // last frame it acknowledged. (Not while waiting for more parts of a meeting still being recorded: the phone
    // acknowledges only every 32 frames, so the last few are never acknowledged until more come.)
    uint32_t rewindTo = 0;
    if ((meeting.next <= total || meeting.endSent) &&
        askble::stall_rewind(meeting.endSent ? meeting.endSeq + 1 : meeting.next, meeting.acked,
                             meeting.progressMs, millis(), &rewindTo)) {
      // acked = the phone's last confirmation, sent = the last frame handed to Bluetooth, refused = how often the
      // stack had no room. Many refusals mean the radio is the bottleneck; none means the phone stopped confirming.
      Serial.printf("[SEND] the phone went quiet: sending meeting %s again from frame %lu (it confirmed %lu, last sent %lu, "
                    "stack refused %lu, link %s)\n",
                    meeting.id, (unsigned long)rewindTo, (unsigned long)meeting.acked, (unsigned long)lastSent,
                    (unsigned long)refused, ble_link::connected() ? "up" : "down");
      refused = 0;
      meeting.next = rewindTo;
      meeting.endSent = false;
      meeting.progressMs = millis();
    }

    bool sent = false;
    if (meeting.next == 0) {
      sent = sendControl(askble::kFrameStart, 0);
      if (sent) meeting.next = 1;
    } else if (meeting.next <= total) {
      // Never more than kSendWindowFrames beyond what the phone confirmed: then the stack's buffers always have room
      // for the phone's own writes, and a phone that stops listening stops the locket after a short while, not after
      // the whole meeting. Waiting here ends when a confirmation comes in (onCommand wakes this task).
      if (meeting.next > meeting.acked + kSendWindowFrames) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(kSendRetryMs));
        continue;
      }
      // Up to kSendBurst frames, stopping at the first one the stack cannot take.
      for (uint32_t n = 0; n < kSendBurst && meeting.next <= total; n++) {
        if (!sendAudio(meeting.next)) {
          refused++;
          break;
        }
        lastSent = meeting.next;
        meeting.next++;
        sent = true;
      }
    } else if (meeting.complete) {
      if (!meeting.endSent || millis() - meeting.endSentMs > kEndRepeatMs) {
        sent = sendControl(askble::kFrameEnd, meeting.endSeq);
        if (sent) {
          meeting.endSent = true;
          meeting.endSentMs = millis();
          lastSent = meeting.endSeq;
        }
      }
    }
    // A meeting still being recorded grows: look for new parts now and then, and when told a part finished.
    if (!meeting.complete && (meeting.next > total || millis() - lastScan > 5000)) {
      scan(&meeting);
      lastScan = millis();
    }
    const bool waiting = meeting.next > total && (!meeting.complete || meeting.endSent);
    if (waiting) {
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
    } else {
      // A frame the stack could not take (out of buffers) is sent again after a short pause.
      vTaskDelay(pdMS_TO_TICKS(sent ? kSendIntervalMs : kSendRetryMs));
    }
  }
}

bool begin() {
  if (task != nullptr) return true;
  commands = xQueueCreate(16, sizeof(Message));
  if (commands == nullptr) return false;
  return xTaskCreatePinnedToCore(run, "ask-sender", kSenderStackBytes, nullptr, 2, &task, 0) == pdPASS;
}

}  // namespace sender
