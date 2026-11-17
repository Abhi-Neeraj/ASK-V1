// The locket's Bluetooth side, on the ESP32 core's BLE library (NimBLE underneath in core 3.3.12).
//
// Pairing (spec 5.2): the phone reads a 16-byte number from PAIR_NONCE, the server emails the person a six-digit
// code made from it, and the phone's operating system asks for that code as the Bluetooth passkey. This locket
// set the same code as its passkey (Display Only, so the phone types it). After the bond, BOND_PROOF shows
// HMAC(secret, "ASK-BONDED" + the number the bond used), which the server checks. Every attempt, right or wrong,
// gets a new number; after 5 wrong codes in 10 minutes the passkey becomes a random value nobody knows until the
// window has passed.
//
// After the final review: the locket never starts pairing by itself (the phone's protected BOND_PROOF read does,
// once the server has emailed the code); a bonded phone is asked to re-encrypt as soon as it connects; a link
// encrypted without the passkey ("Just Works") is dropped and forgotten, and audio goes only to an authenticated
// link; only an attempt that used the passkey counts as a wrong code or earns a proof; notifications go straight
// to the stack so a full buffer is noticed and the frame retried.
//
// NEVER RUN ON A DEVICE where this was written: compiled against the core's headers only.

#include "ble_link.h"

#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLESecurity.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <esp_random.h>
#include <host/ble_gap.h>
#include <host/ble_gatt.h>
#include <host/ble_hs_mbuf.h>
#include <host/ble_store.h>

#include "ble_core.h"
#include "config.h"
#include "identity.h"

namespace ble_link {

static const char* kService = "69bc378a-a795-4f0c-984d-e2cee6c5ea54";
static const char* kInfo = "8242cd7a-098d-4b2d-9335-b9f4df3b7087";
static const char* kPairNonce = "30e12be5-e356-4896-aa4b-c78acd1cb186";
static const char* kControl = "a4e0d5f7-39b8-4614-a440-5398719e89f1";
static const char* kBondProof = "7bac8620-f67c-4bd2-ac89-b6fcc04ebad7";
static const char* kStatus = "72666507-9e9a-40fb-a060-dda51aa64f35";
static const char* kAudio = "3b73557c-0f90-4af2-9416-10b0c91aaf96";

static BLEServer* server = nullptr;
static BLECharacteristic* infoChar = nullptr;
static BLECharacteristic* nonceChar = nullptr;
static BLECharacteristic* proofChar = nullptr;
static BLECharacteristic* statusChar = nullptr;
static BLECharacteristic* audioChar = nullptr;
static void (*commandHandler)(const char*) = nullptr;

static uint8_t secret[32];
static uint8_t nonce[16];      // the number the next pairing uses
static uint32_t nonceMadeMs = 0;
static askble::PasskeyGuard guard = {0, 0};
static String product;

static volatile bool linkUp = false;      // connected
static volatile bool encrypted = false;   // and encrypted with a key made with the passkey (authenticated)
static volatile bool passkeyShown = false;  // this connection's pairing used the passkey
static volatile bool passkeyLocked = false; // the passkey is a random value while too many wrong codes were typed
static volatile uint32_t connectedMs = 0;
static volatile uint16_t connHandle = 0xFFFF;
static volatile uint16_t peerMtu = 23;
static volatile bool knownPeer = false;     // this connection is from a phone that was already bonded

// A new number, and the passkey that goes with it. While too many wrong codes were typed, the passkey is a random
// value that nobody was ever told, so no pairing can succeed.
static void newNonce() {
  esp_fill_random(nonce, sizeof(nonce));
  nonceMadeMs = millis();
  if (nonceChar) nonceChar->setValue(nonce, sizeof(nonce));
  // A TEST build with a fixed PIN (ASK_TEST_PASSKEY set) never locks pairing out: with the lockout, a few cancelled or
  // mistyped tries made the passkey random, so even the right fixed PIN failed for the next ten minutes. A locket
  // built for real use has no fixed PIN (-1) and keeps the lockout.
  passkeyLocked = ASK_TEST_PASSKEY < 0 && !askble::passkey_allowed(&guard, millis());
  const uint32_t passkey =
      askble::choose_passkey(passkeyLocked, ASK_TEST_PASSKEY, askble::pairing_code(secret, nonce), esp_random());
  BLESecurity::setPassKey(true, passkey);
}

static void refreshInfo() {
  char text[96];
  askble::info_text(text, sizeof(text), product.c_str(), ASK_FIRMWARE_VERSION, -1, encrypted);
  if (infoChar) infoChar->setValue((uint8_t*)text, strlen(text));
}

class Security : public BLESecurityCallbacks {
  uint32_t onPassKeyRequest() override { return BLESecurity::getPassKey(); }
  // Called when a new pairing asks this locket for its passkey (never for a bonded phone re-encrypting).
  void onPassKeyNotify(uint32_t) override { passkeyShown = true; }  // never print the passkey
  bool onSecurityRequest() override { return true; }
  bool onConfirmPIN(uint32_t) override { return false; }  // numeric comparison is not used
  // Board package 3.3.10 reports every result here, success or not, with no status (a failure leaves the link
  // unencrypted). 3.3.12 reports only successes here, and every result through the two-argument version below,
  // which this class overrides so nothing is handled twice.
  void onAuthenticationComplete(ble_gap_conn_desc* desc) override { finish(desc, desc->sec_state.encrypted ? 0 : -1); }
  // No `override`: it overrides the library's callback where one exists (3.3.12 and later) and is unused on 3.3.10.
  void onAuthenticationComplete(ble_gap_conn_desc* desc, int status) { finish(desc, status); }

 private:
  void finish(ble_gap_conn_desc* desc, int status) {
#if ASK_TEST_NO_PASSKEY
    // TEST ONLY: there is no passkey, so "encrypted" is as good as a pairing gets. A phone seen for the first time gets
    // the bond proof (as with a passkey); a bonded phone coming back does not.
    if (status == 0 && desc->sec_state.encrypted) {
      encrypted = true;
      if (!knownPeer) {
        knownPeer = true;
        char proof[65];
        askble::bond_proof_hex(secret, nonce, proof);
        if (proofChar) proofChar->setValue((uint8_t*)proof, 64);
        Serial.println(F("[BLE] bond made: proof ready"));
        newNonce();
      }
    } else {
      encrypted = false;
      Serial.printf("[BLE] pairing failed: stack status %d", status);
      Serial.println();
      // The usual cause: the phone still holds a bond from before this locket's bonds were erased (a new ID, a
      // reset). Only forgetting the locket in the phone's Bluetooth settings clears that.
      Serial.println(F("[BLE] If this phone paired with this locket before: forget the ASK-... device in the phone's "
                       "Bluetooth settings, then pair again."));
    }
    refreshInfo();
    return;
#endif
    const bool shown = passkeyShown;
    passkeyShown = false;
    switch (askble::auth_outcome(shown, status, desc->sec_state.encrypted, desc->sec_state.authenticated)) {
      case askble::AuthOutcome::NewBond: {
        // A bond made just now with this number: show its proof, then use a new number for anyone else.
        encrypted = true;
        char proof[65];
        askble::bond_proof_hex(secret, nonce, proof);
        if (proofChar) proofChar->setValue((uint8_t*)proof, 64);
        newNonce();
        break;
      }
      case askble::AuthOutcome::Trusted:
        encrypted = true;  // a bonded phone came back: no proof, same number
        break;
      case askble::AuthOutcome::Reject: {
        encrypted = false;
        Serial.println(F("[BLE] a device paired without the code: disconnected and forgotten"));
        const ble_addr_t peer = desc->peer_id_addr;
        ble_gap_terminate(desc->conn_handle, BLE_ERR_AUTH_FAIL);
        ble_store_util_delete_peer(&peer);
        break;
      }
      case askble::AuthOutcome::Failed:
        encrypted = false;
        askble::passkey_failed(&guard, millis());
        // The passkey itself is never printed. The status code says why the stack gave up (for example a wrong or
        // cancelled code, or a timeout), which tells a mistyped code from a phone that lost its bond.
        Serial.printf("[BLE] pairing failed (wrong code, cancelled, or timed out): stack status %d, lockout %s\n", status,
                      passkeyLocked ? "ON" : "off");
        newNonce();
        break;
      case askble::AuthOutcome::Ignore:
        encrypted = false;  // a phone that lost its bond: it can pair again with a new code
        break;
    }
    refreshInfo();
  }
};

class ServerEvents : public BLEServerCallbacks {
  void onConnect(BLEServer*, ble_gap_conn_desc* desc) override {
    linkUp = true;
    passkeyShown = false;
    connHandle = desc->conn_handle;
    connectedMs = millis();
    // Ask the phone for a fast link (see config.h). The phone may refuse or pick values of its own: audio is then
    // slower, never wrong.
    struct ble_gap_upd_params fast;
    memset(&fast, 0, sizeof(fast));
    fast.itvl_min = kConnIntervalMin;
    fast.itvl_max = kConnIntervalMax;
    fast.latency = kConnLatency;
    fast.supervision_timeout = kConnSupervisionTimeout;
    ble_gap_update_params(desc->conn_handle, &fast);
#if ASK_TEST_NO_PASSKEY
    // TEST ONLY (no-code mode): the link is NOT encrypted and nothing is ever bonded, so there is no bond on either
    // side that can go stale ("forget the ASK device in the phone's settings" is never needed again), and nothing
    // can ask for a pairing that fails. The proof is ready at once, for the number the phone reads. This locket's
    // number does not change while a phone is connected, so the phone's proof and number always belong together.
    encrypted = true;  // here: "the link is usable"
    knownPeer = false;
    if (proofChar) {
      char proof[65];
      askble::bond_proof_hex(secret, nonce, proof);
      proofChar->setValue((uint8_t*)proof, 64);
    }
    refreshInfo();
    return;
#endif
    encrypted = desc->sec_state.encrypted && desc->sec_state.authenticated;
    if (proofChar) proofChar->setValue((uint8_t*)"", 0);
    // A phone this locket is bonded with is asked to re-encrypt at once; anyone else is left alone until the phone
    // app reads BOND_PROOF, which starts pairing only once the code has been emailed.
    struct ble_store_key_sec key;
    struct ble_store_value_sec value;
    memset(&key, 0, sizeof(key));
    key.peer_addr = desc->peer_id_addr;
    const bool bondedPeer = ble_store_read_peer_sec(&key, &value) == 0;
    knownPeer = bondedPeer;
    if (!encrypted && bondedPeer) ble_gap_security_initiate(desc->conn_handle);
    refreshInfo();
  }
  void onDisconnect(BLEServer*, ble_gap_conn_desc*) override {
    linkUp = false;
    encrypted = false;
    passkeyShown = false;
    connHandle = 0xFFFF;
    peerMtu = 23;
    refreshInfo();
  }
  void onMtuChanged(BLEServer*, ble_gap_conn_desc*, uint16_t mtu) override { peerMtu = mtu; }
};

class ControlWrites : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c, ble_gap_conn_desc* desc) override {
#if ASK_TEST_NO_PASSKEY
    const bool trusted = true;  // TEST ONLY: no encryption in the no-code mode
    (void)desc;
#else
    const bool trusted = desc->sec_state.encrypted && desc->sec_state.authenticated;
#endif
    if (!trusted || commandHandler == nullptr) return;
    String value = c->getValue();
    if (value.length() == 0 || value.length() > askble::kMaxCommandBytes) return;
    commandHandler(value.c_str());
  }
};

// BOND_PROOF reads. TEST ONLY (the no-code mode): a phone this locket already has a bond with comes back encrypted
// without a new pairing, so `finish` never made a proof for it, and the app sent the server an empty proof and was
// refused ("Some fields are not valid"). Such a phone now gets the proof for the current number when it reads it.
// With a code (the normal build) this does nothing: a proof is given only for a bond made just now with the emailed
// code. (No `override`, as with onAuthenticationComplete above: if a board package spells this callback differently
// it is simply never called, and the locket behaves as before.)
class ProofReads : public BLECharacteristicCallbacks {
 public:
  void onRead(BLECharacteristic* c, ble_gap_conn_desc* desc) {
#if ASK_TEST_NO_PASSKEY
    (void)desc;
    if (c == nullptr) return;
    if (c->getValue().length() != 0) return;  // the proof set when the phone connected is already in place
    char proof[65];
    askble::bond_proof_hex(secret, nonce, proof);
    c->setValue((uint8_t*)proof, 64);
    // The number is NOT changed here. This read and the pairing's own proof (`finish`) can come in either order;
    // if this one rotated the number, a proof made afterwards would be for the NEW number while the phone told the
    // server the OLD one, and the server refused it ("The locket did not confirm this pairing"). Both now use the
    // same number, so the proof is right whichever comes first.
    Serial.println(F("[BLE] bond proof given to a phone this locket already knows"));
#else
    (void)c;
    (void)desc;
#endif
  }
};

static Security security;
static ServerEvents serverEvents;
static ControlWrites controlWrites;
static ProofReads proofReads;

bool begin(const String& productId) {
  if (!identity::secret(secret)) return false;
  product = productId;
  BLEDevice::init(productId);
  BLEDevice::setMTU(kRequestedMtu);
  BLEDevice::setSecurityCallbacks(&security);
#if ASK_TEST_NO_PASSKEY
  Serial.println(F("WARNING: TEST BUILD (ASK_TEST_NO_PASSKEY): no pairing code, NO encryption and no bonds. Anyone nearby "
                   "can connect, read meetings and send commands, and audio crosses the air unencrypted."));
  BLESecurity::setCapability(ESP_IO_CAP_NONE);
  BLESecurity::setAuthenticationMode(false, false, false);  // never bond: nothing to forget on either side, ever
#else
  BLESecurity::setCapability(ESP_IO_CAP_OUT);  // Display Only: the phone types the code
  BLESecurity::setAuthenticationMode(true, true, true);  // bonding, MITM protection, LE Secure Connections
  BLESecurity::setInitEncryptionKey();
  BLESecurity::setRespEncryptionKey();
  BLESecurity::setForceAuthentication(false);  // pairing starts when the phone reads BOND_PROOF, not on connect
#endif

  server = BLEDevice::createServer();
  if (server == nullptr) return false;
  server->setCallbacks(&serverEvents);
  server->advertiseOnDisconnect(true);
  BLEService* service = server->createService(kService);

  infoChar = service->createCharacteristic(kInfo, BLECharacteristic::PROPERTY_READ);
  nonceChar = service->createCharacteristic(kPairNonce, BLECharacteristic::PROPERTY_READ);
#if ASK_TEST_NO_PASSKEY
  // TEST ONLY: plain characteristics. Nothing asks the phone's system to pair or encrypt, so there is no pairing
  // dialog, no bond to go stale, and no step that can fail with a "stack status" error.
  BLECharacteristic* control = service->createCharacteristic(kControl, BLECharacteristic::PROPERTY_WRITE);
  control->setCallbacks(&controlWrites);
  proofChar = service->createCharacteristic(kBondProof, BLECharacteristic::PROPERTY_READ);
  proofChar->setCallbacks(&proofReads);
  statusChar = service->createCharacteristic(
      kStatus, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
#else
  BLECharacteristic* control = service->createCharacteristic(
      kControl, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_ENC |
                    BLECharacteristic::PROPERTY_WRITE_AUTHEN);
  control->setCallbacks(&controlWrites);
  proofChar = service->createCharacteristic(
      kBondProof, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_READ_ENC |
                      BLECharacteristic::PROPERTY_READ_AUTHEN);
  proofChar->setCallbacks(&proofReads);
  statusChar = service->createCharacteristic(
      kStatus, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_READ_ENC |
                   BLECharacteristic::PROPERTY_NOTIFY);
#endif
  audioChar = service->createCharacteristic(kAudio, BLECharacteristic::PROPERTY_NOTIFY);

  newNonce();
  refreshInfo();
  service->start();

  BLEAdvertising* advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(kService);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();
#if ASK_TEST_NO_PASSKEY
  // Drop every bond this locket kept from earlier builds (they were made with the old, encrypted setup): a phone that
  // still remembers one is told "no key" cleanly, instead of the locket and the phone each holding half of a bond.
  ble_store_clear();
#endif
  Serial.printf("[BLE] advertising as %s\n", productId.c_str());
  return true;
}

bool connected() { return linkUp && encrypted; }
uint16_t mtu() { return peerMtu; }

// Straight to the stack (not BLECharacteristic::notify, which gives no result and walks lists the Bluetooth task
// changes): false when the stack is out of buffers or the link is gone, so the caller sends the same frame again.
static bool notifyRaw(BLECharacteristic* c, const uint8_t* data, size_t length) {
  const uint16_t handle = connHandle;
  if (c == nullptr || handle == 0xFFFF) return false;
  struct os_mbuf* om = ble_hs_mbuf_from_flat(data, (uint16_t)length);
  if (om == nullptr) return false;
  return ble_gatts_notify_custom(handle, c->getHandle(), om) == 0;  // the stack frees `om` either way
}

bool notifyAudio(const uint8_t* frame, size_t length) {
  if (!connected() || length + 3 > peerMtu) return false;
  // Never take the stack's last few buffers: the phone's confirmations need one to be received, and a lost
  // confirmation is what made the locket think the phone had gone quiet (log: "ble_att_svr_pkt rc=6").
  if (os_msys_num_free() < kMbufReserve) return false;
  return notifyRaw(audioChar, frame, length);
}

void notifyStatus(const char* text) {
  if (statusChar == nullptr) return;
  statusChar->setValue((uint8_t*)text, strlen(text));
  if (connected()) notifyRaw(statusChar, (const uint8_t*)text, strlen(text));
}

void setCommandHandler(void (*handler)(const char* line)) { commandHandler = handler; }

void forgetCurrent() {
  const uint16_t handle = connHandle;
  if (!linkUp || handle == 0xFFFF) return;
  // The phone's identity address as it is now (on a first pairing it is only known after the bond).
  struct ble_gap_conn_desc desc;
  if (ble_gap_conn_find(handle, &desc) != 0) return;
  const ble_addr_t peer = desc.peer_id_addr;
  if (server) server->disconnect(handle);
  ble_store_util_delete_peer(&peer);
  Serial.println(F("[BLE] the phone asked to be forgotten"));
}

void forgetAll() {
  if (server && connHandle != 0xFFFF) server->disconnect(connHandle);
  ble_store_clear();
}

void tick() {
  // A fresh number now and then, but never in the middle of someone pairing; and the real passkey back as soon as
  // the wrong-code window has passed.
  if (!linkUp && millis() - nonceMadeMs > kNonceRotateMs) newNonce();
  if (passkeyLocked && askble::passkey_allowed(&guard, millis())) newNonce();
  // Advertising stops while connected: a device that connects and never pairs is dropped so the owner's phone can
  // find the locket again.
  const uint16_t handle = connHandle;
  if (linkUp && !encrypted && handle != 0xFFFF && millis() - connectedMs > kUnpairedLinkMs) {
    Serial.println(F("[BLE] a device stayed connected without pairing: disconnected"));
    if (server) server->disconnect(handle);
  }
}

}  // namespace ble_link
