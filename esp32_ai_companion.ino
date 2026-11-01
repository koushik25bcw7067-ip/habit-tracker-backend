#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Preferences.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <driver/i2s.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <time.h>

// Configure these locally. Never commit real Wi-Fi credentials.
#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASS "YOUR_WIFI_PASSWORD"
#define PAIRING_CODE ""
#define BACKEND_URL "https://habit-tracker-backend-hks4.onrender.com/companion/voice/turn"
#define CLAIM_URL "https://habit-tracker-backend-hks4.onrender.com/companion/claim"

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define PIN_SDA 18
#define PIN_SCL 19
#define BTN_TALK 23
#define BTN_STOP 16

#define I2S_MIC_PORT I2S_NUM_0
#define I2S_SPK_PORT I2S_NUM_1
#define PIN_MIC_SCK 33
#define PIN_MIC_WS 32
#define PIN_MIC_SD 22
#define PIN_SPK_BCLK 26
#define PIN_SPK_LRC 25
#define PIN_SPK_DIN 27

#define MIC_RATE 16000
#define SPK_RATE 24000
#define MAX_RECORD_SECONDS 15
#define MAX_PCM_BYTES (MIC_RATE * MAX_RECORD_SECONDS * 2UL)
#define WAV_HEADER_SIZE 44
#define MAX_RESPONSE_PCM_BYTES (1024UL * 1024UL)
#define HTTP_TIMEOUT_MS 60000
#define PLAYBACK_GAIN_NUM 3
#define PLAYBACK_GAIN_DEN 2

// GTS Root R4 signs the current Google Trust Services WE1 chain for onrender.com.
static const char GTS_ROOT_R4[] PROGMEM = R"PEM(
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvVYRg1rkDdc/eJkTBa6zzuhXyi
QHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHci8nHc8iMai/lxKvR
HYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D
9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8
p/SgguMh1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD
-----END CERTIFICATE-----
)PEM";

enum Face {
  IDLE,
  LISTEN,
  THINK,
  SPEAK,
  ERROR_FACE
};

static Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
static Preferences preferences;
static bool oledOk = false;
static bool micReady = false;
static bool speakerReady = false;
static bool processing = false;
static bool tlsClockReady = false;
static uint8_t *audioBuffer = nullptr;
static int16_t *microphonePcm = nullptr;
static size_t pcmCapacityBytes = 0;
static char deviceCredential[129] = {};
static char pendingCredential[65] = {};
static int32_t micSamples[256];
static bool micRawLogged = false;
static uint8_t playbackInput[1024];
static int16_t playbackOutput[1024];

static const char *responseHeaderNames[] = {
    "Content-Type",
    "X-Companion-Reply",
    "X-Companion-Transcript",
    "X-Companion-Status",
    "X-Companion-Action-Type",
    "X-Companion-Action-Id",
    "X-Companion-Conversation-Id",
};

static void put16(uint8_t *data, uint16_t value) {
  data[0] = static_cast<uint8_t>(value);
  data[1] = static_cast<uint8_t>(value >> 8);
}

static void put32(uint8_t *data, uint32_t value) {
  data[0] = static_cast<uint8_t>(value);
  data[1] = static_cast<uint8_t>(value >> 8);
  data[2] = static_cast<uint8_t>(value >> 16);
  data[3] = static_cast<uint8_t>(value >> 24);
}

static uint16_t get16(const uint8_t *data) {
  return static_cast<uint16_t>(data[0]) |
         (static_cast<uint16_t>(data[1]) << 8);
}

static uint32_t get32(const uint8_t *data) {
  return static_cast<uint32_t>(data[0]) |
         (static_cast<uint32_t>(data[1]) << 8) |
         (static_cast<uint32_t>(data[2]) << 16) |
         (static_cast<uint32_t>(data[3]) << 24);
}

static void writeWavHeader(uint8_t *header, uint32_t dataBytes,
                           uint32_t sampleRate) {
  memcpy(header, "RIFF", 4);
  put32(header + 4, dataBytes + 36);
  memcpy(header + 8, "WAVE", 4);
  memcpy(header + 12, "fmt ", 4);
  put32(header + 16, 16);
  put16(header + 20, 1);
  put16(header + 22, 1);
  put32(header + 24, sampleRate);
  put32(header + 28, sampleRate * 2);
  put16(header + 32, 2);
  put16(header + 34, 16);
  memcpy(header + 36, "data", 4);
  put32(header + 40, dataBytes);
}

static void drawStatus(const char *title, const char *subtitle, Face face) {
  if (!oledOk) {
    return;
  }

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println(title);

  if (face == LISTEN) {
    display.fillRoundRect(24, 18, 26, 32, 10, SSD1306_WHITE);
    display.fillRoundRect(78, 18, 26, 32, 10, SSD1306_WHITE);
  } else if (face == THINK) {
    display.drawCircle(64, 36, 18, SSD1306_WHITE);
  } else if (face == SPEAK) {
    display.drawCircle(37, 32, 14, SSD1306_WHITE);
    display.drawCircle(91, 32, 14, SSD1306_WHITE);
    display.fillCircle(64, 44, 4, SSD1306_WHITE);
  } else if (face == ERROR_FACE) {
    display.drawLine(28, 26, 48, 44, SSD1306_WHITE);
    display.drawLine(48, 26, 28, 44, SSD1306_WHITE);
    display.drawLine(80, 26, 100, 44, SSD1306_WHITE);
    display.drawLine(100, 26, 80, 44, SSD1306_WHITE);
  } else {
    display.fillRoundRect(26, 22, 24, 26, 8, SSD1306_WHITE);
    display.fillRoundRect(78, 22, 24, 26, 8, SSD1306_WHITE);
  }

  if (subtitle) {
    display.setCursor(0, 55);
    display.print(subtitle);
  }
  display.display();
}

static String asciiForOled(const String &text) {
  String result;
  result.reserve(text.length());
  for (size_t i = 0; i < text.length(); ++i) {
    const uint8_t ch = static_cast<uint8_t>(text[i]);
    if (ch >= 0x20 && ch <= 0x7e) {
      result += static_cast<char>(ch);
    } else if ((ch & 0xc0) != 0x80) {
      result += '?';
    }
  }
  return result;
}

static void drawTextPage(const String &title, const String &text) {
  if (!oledOk) {
    return;
  }

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println(title.substring(0, 21));

  String safeText = asciiForOled(text);
  size_t offset = 0;
  int y = 12;
  while (offset < safeText.length() && y <= 56) {
    size_t end = offset + 21;
    if (end < safeText.length()) {
      int split = safeText.lastIndexOf(' ', end);
      if (split > static_cast<int>(offset)) {
        end = static_cast<size_t>(split);
      }
    } else {
      end = safeText.length();
    }
    display.setCursor(0, y);
    display.println(safeText.substring(offset, end));
    offset = end;
    while (offset < safeText.length() && safeText[offset] == ' ') {
      ++offset;
    }
    y += 10;
  }
  display.display();
}

static void showCompanionHeaders(const String &status, const String &transcript,
                                 const String &reply, const String &actionType,
                                 const String &actionId) {
  drawTextPage("STATUS: " + status, "Transcript: " + transcript);
  delay(1200);
  drawTextPage("COMPANION REPLY", reply);
  if (actionType.length()) {
    delay(1200);
    drawTextPage("ACTION: " + actionType, actionId);
  }
}

static void printMemory(const char *tag) {
  Serial.printf("[MEM] %s free=%u min=%u largest=%u psram=%u\n", tag,
                static_cast<unsigned>(ESP.getFreeHeap()),
                static_cast<unsigned>(ESP.getMinFreeHeap()),
                static_cast<unsigned>(
                    heap_caps_get_largest_free_block(MALLOC_CAP_8BIT)),
                static_cast<unsigned>(ESP.getFreePsram()));
}

static bool allocateAudioBuffer() {
  const size_t freeInternal =
      heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  const size_t largestInternal =
      heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  Serial.printf("[MEM] Before audio allocation: free_internal=%u largest_internal=%u\n",
                static_cast<unsigned>(freeInternal),
                static_cast<unsigned>(largestInternal));

  audioBuffer = static_cast<uint8_t *>(heap_caps_malloc(
      WAV_HEADER_SIZE + MAX_PCM_BYTES,
      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (audioBuffer) {
    pcmCapacityBytes = MAX_PCM_BYTES;
    Serial.println("[MEM] Audio buffer allocated in PSRAM (15 seconds)");
  } else {
    static const size_t fallbackSizes[] = {
        64000, 48000, 32000, 24000, 16000, 8000};
    for (const size_t candidate : fallbackSizes) {
      audioBuffer = static_cast<uint8_t *>(heap_caps_malloc(
          WAV_HEADER_SIZE + candidate,
          MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
      if (audioBuffer) {
        pcmCapacityBytes = candidate;
        break;
      }
    }
    if (!audioBuffer) {
      const size_t freeNow =
          heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
      const size_t largestNow =
          heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
      Serial.printf(
          "[MEM ERR] Audio allocation failed: free_internal=%u largest_internal=%u\n",
          static_cast<unsigned>(freeNow),
          static_cast<unsigned>(largestNow));
      return false;
    }
    const uint32_t seconds =
        static_cast<uint32_t>(pcmCapacityBytes / (MIC_RATE * 2UL));
    Serial.printf(
        "[MEM] PSRAM unavailable; using %u bytes internal RAM (~%u sec)\n",
        static_cast<unsigned>(pcmCapacityBytes),
        static_cast<unsigned>(seconds));
  }
  microphonePcm =
      reinterpret_cast<int16_t *>(audioBuffer + WAV_HEADER_SIZE);
  Serial.printf(
      "[MEM] Audio buffer ready: pcm_capacity=%u bytes; largest_internal_after=%u\n",
      static_cast<unsigned>(pcmCapacityBytes),
      static_cast<unsigned>(heap_caps_get_largest_free_block(
          MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)));
  return true;
}

static bool loadDeviceCredential() {
  if (!preferences.begin("voice-companion", false)) {
    Serial.println("[SESSION ERR] NVS preferences unavailable");
    return false;
  }
  String stored = preferences.getString("credential", "");
  String pending = preferences.getString("claim", "");
  preferences.end();
  if (stored.length() >= 43 && stored.length() < sizeof(deviceCredential)) {
    stored.toCharArray(deviceCredential, sizeof(deviceCredential));
    Serial.println("[PAIR] Saved device credential loaded from NVS");
  } else {
    deviceCredential[0] = '\0';
    Serial.println("[PAIR] No paired device credential in NVS");
  }
  if (pending.length() == 64) {
    pending.toCharArray(pendingCredential, sizeof(pendingCredential));
  }
  return true;
}

static String readErrorBody(HTTPClient &http);

static void logHttpsFailure(const char *operation, int statusCode,
                            WiFiClientSecure &client) {
  char tlsError[128] = {};
  const int tlsErrorCode = client.lastError(tlsError, sizeof(tlsError));
  Serial.printf(
      "[HTTPS ERR] %s: %s (%d); WiFi=%d RSSI=%d time=%lu TLS=%d %s\n",
      operation,
      HTTPClient::errorToString(statusCode).c_str(),
      statusCode,
      static_cast<int>(WiFi.status()),
      WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0,
      static_cast<unsigned long>(time(nullptr)),
      tlsErrorCode,
      tlsError[0] ? tlsError : "no TLS detail");
}

static bool claimDevice() {
  if (deviceCredential[0] != '\0') {
    return true;
  }
  if (strlen(PAIRING_CODE) != 12) {
    static bool warned = false;
    if (!warned) {
      Serial.println("[PAIR ERR] Set the 12-character code from Profile in PAIRING_CODE");
      warned = true;
    }
    return false;
  }
  if (WiFi.status() != WL_CONNECTED || !tlsClockReady) {
    return false;
  }

  if (pendingCredential[0] == '\0') {
    uint8_t randomBytes[32];
    static const char hex[] = "0123456789abcdef";
    esp_fill_random(randomBytes, sizeof(randomBytes));
    for (size_t i = 0; i < sizeof(randomBytes); ++i) {
      pendingCredential[i * 2] = hex[randomBytes[i] >> 4];
      pendingCredential[i * 2 + 1] = hex[randomBytes[i] & 0x0f];
    }
    pendingCredential[64] = '\0';
    if (!preferences.begin("voice-companion", false)) {
      Serial.println("[PAIR ERR] Could not open NVS for pending credential");
      pendingCredential[0] = '\0';
      return false;
    }
    const size_t saved =
        preferences.putString("claim", pendingCredential);
    preferences.end();
    if (saved != strlen(pendingCredential)) {
      Serial.println("[PAIR ERR] Could not persist pending credential");
      pendingCredential[0] = '\0';
      return false;
    }
  }

  WiFiClientSecure client;
  client.useBuiltinCACertBundle();
  HTTPClient http;
  http.setReuse(false);
  http.setTimeout(15000);
  http.setConnectTimeout(20000);
  if (!http.begin(client, CLAIM_URL)) {
    Serial.println("[PAIR ERR] HTTPS claim setup failed");
    logHttpsFailure("claim begin", HTTPC_ERROR_CONNECTION_REFUSED, client);
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "close");
  const String payload =
      String("{\"pairing_code\":\"") + PAIRING_CODE +
      "\",\"credential\":\"" + pendingCredential + "\"}";
  const int statusCode = http.POST(payload);
  if (statusCode != HTTP_CODE_OK) {
    Serial.printf("[PAIR ERR] Claim request failed: HTTP %d\n", statusCode);
    logHttpsFailure("claim POST", statusCode, client);
    String body = readErrorBody(http);
    if (body.length()) {
      Serial.printf("[PAIR ERR] %.160s\n", body.c_str());
    }
    http.end();
    client.stop();
    return false;
  }
  http.end();
  client.stop();

  if (!preferences.begin("voice-companion", false)) {
    Serial.println("[PAIR ERR] Could not open NVS to save device credential");
    return false;
  }
  const size_t saved = preferences.putString("credential", pendingCredential);
  if (saved == strlen(pendingCredential)) {
    preferences.remove("claim");
  }
  preferences.end();
  if (saved != strlen(pendingCredential)) {
    Serial.println("[PAIR ERR] Could not persist device credential");
    return false;
  }
  memcpy(deviceCredential, pendingCredential, sizeof(pendingCredential));
  Serial.println("[PAIR] Device paired; credential saved in NVS");
  return true;
}

static bool syncClock() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  const uint32_t started = millis();
  while (millis() - started < 15000) {
    if (time(nullptr) > 1704067200) {
      tlsClockReady = true;
      Serial.println("[TIME] NTP time synchronized");
      return true;
    }
    delay(250);
  }
  tlsClockReady = false;
  Serial.println("[TIME ERR] NTP time unavailable; HTTPS certificate checks need time");
  return false;
}

static bool connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!tlsClockReady) {
      syncClock();
    }
    return true;
  }

  drawStatus("WIFI", "Connecting...", THINK);
  WiFi.mode(WIFI_STA);
  for (int attempt = 1; attempt <= 3; ++attempt) {
    Serial.printf("[WIFI] Attempt %d/3\n", attempt);
    WiFi.disconnect(false, false);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    const uint32_t started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
      delay(250);
      yield();
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("[WIFI] Connected. IP: %s\n",
                    WiFi.localIP().toString().c_str());
      Serial.printf("[WIFI] RSSI=%d dBm\n", WiFi.RSSI());
      syncClock();
      return true;
    }
    Serial.println("[WIFI] Attempt timed out");
    delay(1000 * attempt);
  }

  Serial.println("[WIFI ERR] Could not connect; will retry in loop");
  drawStatus("WIFI OFFLINE", "Check network", ERROR_FACE);
  return false;
}

static bool setupI2S() {
  i2s_config_t micConfig = {};
  micConfig.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  micConfig.sample_rate = MIC_RATE;
  micConfig.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  micConfig.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  micConfig.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  micConfig.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  micConfig.dma_buf_count = 4;
  micConfig.dma_buf_len = 128;
  micConfig.use_apll = false;
  micConfig.tx_desc_auto_clear = false;
  micConfig.fixed_mclk = 0;

  i2s_pin_config_t micPins = {};
  micPins.mck_io_num = I2S_PIN_NO_CHANGE;
  micPins.bck_io_num = PIN_MIC_SCK;
  micPins.ws_io_num = PIN_MIC_WS;
  micPins.data_out_num = I2S_PIN_NO_CHANGE;
  micPins.data_in_num = PIN_MIC_SD;

  esp_err_t error = i2s_driver_install(I2S_MIC_PORT, &micConfig, 0, nullptr);
  Serial.printf("[MIC I2S] install=%d\n", static_cast<int>(error));
  if (error != ESP_OK) {
    return false;
  }
  error = i2s_set_pin(I2S_MIC_PORT, &micPins);
  Serial.printf("[MIC I2S] pins=%d\n", static_cast<int>(error));
  if (error != ESP_OK) {
    return false;
  }
  micReady = true;

  i2s_config_t speakerConfig = {};
  speakerConfig.mode =
      static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  speakerConfig.sample_rate = SPK_RATE;
  speakerConfig.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  speakerConfig.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  speakerConfig.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  speakerConfig.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  speakerConfig.dma_buf_count = 4;
  speakerConfig.dma_buf_len = 128;
  speakerConfig.use_apll = false;
  speakerConfig.tx_desc_auto_clear = true;
  speakerConfig.fixed_mclk = 0;

  i2s_pin_config_t speakerPins = {};
  speakerPins.mck_io_num = I2S_PIN_NO_CHANGE;
  speakerPins.bck_io_num = PIN_SPK_BCLK;
  speakerPins.ws_io_num = PIN_SPK_LRC;
  speakerPins.data_out_num = PIN_SPK_DIN;
  speakerPins.data_in_num = I2S_PIN_NO_CHANGE;

  error = i2s_driver_install(I2S_SPK_PORT, &speakerConfig, 0, nullptr);
  Serial.printf("[SPK I2S] install=%d\n", static_cast<int>(error));
  if (error != ESP_OK) {
    return false;
  }
  error = i2s_set_pin(I2S_SPK_PORT, &speakerPins);
  Serial.printf("[SPK I2S] pins=%d\n", static_cast<int>(error));
  if (error != ESP_OK) {
    return false;
  }
  speakerReady = true;
  i2s_zero_dma_buffer(I2S_MIC_PORT);
  i2s_zero_dma_buffer(I2S_SPK_PORT);
  return true;
}

static bool recordAudio(size_t &sampleCount, int &peak, bool &cancelled) {
  if (!micReady || !microphonePcm) {
    return false;
  }

  size_t count = 0;
  int maxPeak = 0;
  int64_t dcSum = 0;
  uint32_t dcCount = 0;
  uint32_t rawPeakLeft = 0;
  uint32_t rawPeakRight = 0;
  int32_t firstRawLeft = 0;
  int32_t firstRawRight = 0;
  bool haveFirstRaw = false;
  uint32_t lastPressed = millis();
  cancelled = false;
  i2s_zero_dma_buffer(I2S_MIC_PORT);
  delay(40);
  Serial.println("[REC] Recording at 16 kHz; release TALK to finish");

  while (count < pcmCapacityBytes / sizeof(int16_t)) {
    if (digitalRead(BTN_STOP) == LOW) {
      cancelled = true;
      break;
    }
    if (digitalRead(BTN_TALK) == LOW) {
      lastPressed = millis();
    } else if (millis() - lastPressed > 180) {
      break;
    }

    size_t bytesRead = 0;
    esp_err_t error = i2s_read(I2S_MIC_PORT, micSamples, sizeof(micSamples),
                               &bytesRead, pdMS_TO_TICKS(100));
    if (error != ESP_OK || bytesRead < 8) {
      continue;
    }

    const size_t frames = bytesRead / (2 * sizeof(int32_t));
    for (size_t i = 0; i < frames && count < pcmCapacityBytes / 2; ++i) {
      const int32_t rawLeft = micSamples[2 * i];
      const int32_t rawRight = micSamples[2 * i + 1];
      if (!haveFirstRaw) {
        firstRawLeft = rawLeft;
        firstRawRight = rawRight;
        haveFirstRaw = true;
      }
      const uint32_t leftMagnitude = static_cast<uint32_t>(
          rawLeft < 0 ? -static_cast<int64_t>(rawLeft) : rawLeft);
      const uint32_t rightMagnitude = static_cast<uint32_t>(
          rawRight < 0 ? -static_cast<int64_t>(rawRight) : rawRight);
      rawPeakLeft = max(rawPeakLeft, leftMagnitude);
      rawPeakRight = max(rawPeakRight, rightMagnitude);

      const int32_t left = rawLeft >> 14;
      const int32_t right = rawRight >> 14;
      const int32_t selected = (abs(left) > abs(right)) ? left : right;
      const int32_t value = constrain(selected, -32768, 32767);
      dcSum += value;
      ++dcCount;
      microphonePcm[count++] = static_cast<int16_t>(value);
    }
    yield();
  }

  if (!micRawLogged && haveFirstRaw) {
    Serial.printf(
        "[MIC RAW] first L=%ld R=%ld; peak L=%u R=%u; converted shift=14\n",
        static_cast<long>(firstRawLeft),
        static_cast<long>(firstRawRight),
        static_cast<unsigned>(rawPeakLeft),
        static_cast<unsigned>(rawPeakRight));
    micRawLogged = true;
  }

  if (dcCount) {
    const int32_t dc = static_cast<int32_t>(dcSum / dcCount);
    for (size_t i = 0; i < count; ++i) {
      int32_t value = static_cast<int32_t>(microphonePcm[i]) - dc;
      value = constrain(value, -32768, 32767);
      microphonePcm[i] = static_cast<int16_t>(value);
      const int magnitude = abs(value);
      if (magnitude > maxPeak) {
        maxPeak = magnitude;
      }
    }
  }

  sampleCount = count;
  peak = maxPeak;
  Serial.printf("[REC] samples=%u peak=%d pcm_bytes=%u\n",
                static_cast<unsigned>(count), maxPeak,
                static_cast<unsigned>(count * 2));
  return count > 0 && !cancelled;
}

static void normalizeAudio(size_t samples, int peak) {
  if (peak < 100) {
    return;
  }
  float gain = 18000.0f / static_cast<float>(peak);
  gain = constrain(gain, 1.0f, 3.0f);
  for (size_t i = 0; i < samples; ++i) {
    int32_t value = static_cast<int32_t>(microphonePcm[i] * gain);
    value = constrain(value, -32768, 32767);
    microphonePcm[i] = static_cast<int16_t>(value);
  }
}

static bool readExact(NetworkClient *stream, uint8_t *destination,
                      size_t wanted, HTTPClient &http, uint32_t timeoutMs) {
  size_t received = 0;
  const uint32_t started = millis();
  while (received < wanted && millis() - started < timeoutMs) {
    const int available = stream->available();
    if (available > 0) {
      const size_t amount =
          min(static_cast<size_t>(available), wanted - received);
      const int count = stream->read(destination + received, amount);
      if (count > 0) {
        received += static_cast<size_t>(count);
      }
    } else {
      if (!http.connected() && stream->available() <= 0) {
        break;
      }
      delay(2);
      yield();
    }
  }
  return received == wanted;
}

static bool playResponseAudio(HTTPClient &http) {
  if (!speakerReady) {
    return false;
  }

  auto *stream = http.getStreamPtr();
  if (!stream) {
    Serial.println("[PLAY ERR] Null HTTP stream");
    return false;
  }

  uint8_t header[WAV_HEADER_SIZE];
  if (!readExact(stream, header, sizeof(header), http, 10000)) {
    Serial.println("[PLAY ERR] WAV header timeout/incomplete");
    return false;
  }
  if (memcmp(header, "RIFF", 4) != 0 ||
      memcmp(header + 8, "WAVE", 4) != 0 ||
      memcmp(header + 12, "fmt ", 4) != 0 ||
      memcmp(header + 36, "data", 4) != 0) {
    Serial.println("[PLAY ERR] Invalid WAV header");
    return false;
  }

  const uint16_t format = get16(header + 20);
  const uint16_t channels = get16(header + 22);
  const uint32_t sampleRate = get32(header + 24);
  const uint16_t bits = get16(header + 34);
  const uint32_t dataBytes = get32(header + 40);
  Serial.printf("[WAV] format=%u channels=%u rate=%u bits=%u data=%u\n",
                format, channels, sampleRate, bits,
                static_cast<unsigned>(dataBytes));
  if (format != 1 || channels != 1 || bits != 16 ||
      sampleRate < 8000 || sampleRate > 48000 || dataBytes == 0 ||
      (dataBytes & 1) || dataBytes > MAX_RESPONSE_PCM_BYTES) {
    Serial.println("[PLAY ERR] Unsupported or oversized PCM WAV");
    return false;
  }

  esp_err_t error = i2s_set_clk(I2S_SPK_PORT, sampleRate,
                                I2S_BITS_PER_SAMPLE_16BIT,
                                I2S_CHANNEL_STEREO);
  Serial.printf("[SPK] set clock=%d\n", static_cast<int>(error));
  if (error != ESP_OK) {
    return false;
  }

  drawStatus("SPEAKING", "Press STOP to cancel", SPEAK);
  uint32_t remaining = dataBytes;
  uint32_t totalRead = 0;
  bool stopped = false;
  while (remaining > 0) {
    if (digitalRead(BTN_STOP) == LOW) {
      Serial.println("[PLAY] Stopped by button");
      stopped = true;
      break;
    }

    size_t wanted = min(static_cast<size_t>(remaining),
                        sizeof(playbackInput));
    wanted &= ~static_cast<size_t>(1);
    if (wanted == 0) {
      break;
    }

    size_t received = 0;
    const uint32_t started = millis();
    while (received < wanted && millis() - started < 10000) {
      const int available = stream->available();
      if (available > 0) {
        const size_t amount =
            min(static_cast<size_t>(available), wanted - received);
        const int count = stream->read(playbackInput + received, amount);
        if (count > 0) {
          received += static_cast<size_t>(count);
        }
      } else {
        if (!http.connected() && stream->available() <= 0) {
          break;
        }
        delay(2);
        yield();
      }
    }
    received &= ~static_cast<size_t>(1);
    if (received == 0) {
      Serial.println("[PLAY ERR] Stream ended before WAV data completed");
      break;
    }

    const size_t samples = received / 2;
    const int16_t *mono =
        reinterpret_cast<const int16_t *>(playbackInput);
    for (size_t i = 0; i < samples; ++i) {
      int32_t value =
          (static_cast<int32_t>(mono[i]) * PLAYBACK_GAIN_NUM) /
          PLAYBACK_GAIN_DEN;
      value = constrain(value, -30000, 30000);
      playbackOutput[2 * i] = static_cast<int16_t>(value);
      playbackOutput[2 * i + 1] = static_cast<int16_t>(value);
    }

    const size_t bytesToWrite = samples * 4;
    size_t writtenTotal = 0;
    while (writtenTotal < bytesToWrite) {
      size_t written = 0;
      error = i2s_write(
          I2S_SPK_PORT,
          reinterpret_cast<uint8_t *>(playbackOutput) + writtenTotal,
          bytesToWrite - writtenTotal, &written, pdMS_TO_TICKS(500));
      if (error != ESP_OK || written == 0) {
        Serial.printf("[PLAY ERR] I2S write=%d bytes=%u\n",
                      static_cast<int>(error),
                      static_cast<unsigned>(written));
        break;
      }
      writtenTotal += written;
    }
    if (writtenTotal != bytesToWrite) {
      break;
    }

    remaining -= static_cast<uint32_t>(received);
    totalRead += static_cast<uint32_t>(received);
    if ((totalRead % 8192) < received) {
      Serial.printf("[PLAY] %u/%u bytes\n",
                    static_cast<unsigned>(totalRead),
                    static_cast<unsigned>(dataBytes));
      printMemory("during-play");
    }
    yield();
  }

  if (remaining == 0) {
    delay(100);
  }
  i2s_zero_dma_buffer(I2S_SPK_PORT);
  Serial.printf("[PLAY] PCM bytes=%u expected=%u remaining=%u stopped=%d\n",
                static_cast<unsigned>(totalRead),
                static_cast<unsigned>(dataBytes),
                static_cast<unsigned>(remaining), stopped);
  printMemory("after-play");
  return remaining == 0 && !stopped;
}

static String decodeHeader(const String &encoded) {
  String decoded;
  const size_t reserveLength =
      encoded.length() < 512 ? encoded.length() : 512;
  decoded.reserve(reserveLength);
  for (size_t i = 0; i < encoded.length() && decoded.length() < 512; ++i) {
    if (encoded[i] == '%' && i + 2 < encoded.length()) {
      char hex[3] = {encoded[i + 1], encoded[i + 2], '\0'};
      char *end = nullptr;
      const long value = strtol(hex, &end, 16);
      if (end && *end == '\0') {
        decoded += static_cast<char>(value);
        i += 2;
        continue;
      }
    }
    decoded += encoded[i] == '+' ? ' ' : encoded[i];
  }
  return decoded;
}

static String readErrorBody(HTTPClient &http) {
  auto *stream = http.getStreamPtr();
  String body;
  body.reserve(180);
  if (!stream) {
    return body;
  }
  const uint32_t started = millis();
  while (body.length() < 180 && millis() - started < 1000) {
    while (stream->available() && body.length() < 180) {
      body += static_cast<char>(stream->read());
    }
    if (!http.connected()) {
      break;
    }
    delay(5);
  }
  return body;
}

static bool sendVoice(size_t samples) {
  if (WiFi.status() != WL_CONNECTED && !connectWiFi()) {
    drawStatus("WIFI ERROR", "Reconnect failed", ERROR_FACE);
    return false;
  }
  if (!tlsClockReady && !syncClock()) {
    drawStatus("TIME ERROR", "HTTPS needs NTP", ERROR_FACE);
    return false;
  }

  const uint32_t dataBytes = static_cast<uint32_t>(samples * 2);
  writeWavHeader(audioBuffer, dataBytes, MIC_RATE);
  const size_t totalBytes = WAV_HEADER_SIZE + dataBytes;
  Serial.printf("[HTTP] POST %u WAV bytes\n",
                static_cast<unsigned>(totalBytes));
  printMemory("before-http");
  drawStatus("THINKING", "Contacting companion", THINK);

  WiFiClientSecure client;
  client.useBuiltinCACertBundle();
  HTTPClient http;
  http.setReuse(false);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(15000);
  http.useHTTP10(true);
  http.collectHeaders(responseHeaderNames,
                      sizeof(responseHeaderNames) /
                          sizeof(responseHeaderNames[0]));
  if (!http.begin(client, BACKEND_URL)) {
    Serial.println("[HTTP ERR] HTTPS begin failed");
    logHttpsFailure("voice begin", HTTPC_ERROR_CONNECTION_REFUSED, client);
    drawStatus("HTTPS ERROR", "Connection setup", ERROR_FACE);
    return false;
  }

  http.addHeader("Content-Type", "audio/wav");
  http.addHeader("Authorization", String("Bearer ") + deviceCredential);
  http.addHeader("Connection", "close");
  const int statusCode = http.POST(audioBuffer, totalBytes);
  Serial.printf("[HTTP] status=%d content-length=%d\n", statusCode,
                http.getSize());

  bool played = false;
  if (statusCode == HTTP_CODE_OK) {
    const String contentType = http.header("Content-Type");
    const String companionStatus = http.header("X-Companion-Status");
    const String transcript =
        decodeHeader(http.header("X-Companion-Transcript"));
    const String reply =
        decodeHeader(http.header("X-Companion-Reply"));
    const String actionType = http.header("X-Companion-Action-Type");
    const String actionId = http.header("X-Companion-Action-Id");
    Serial.printf("[VOICE] status=%s action=%s\n",
                  companionStatus.c_str(), actionType.c_str());
    Serial.printf("[VOICE] transcript: %.180s\n", transcript.c_str());
    Serial.printf("[VOICE] reply: %.180s\n", reply.c_str());

    if (!contentType.startsWith("audio/wav")) {
      Serial.printf("[HTTP ERR] Unexpected content type: %s\n",
                    contentType.c_str());
      drawStatus("AUDIO ERROR", "Expected WAV", ERROR_FACE);
    } else {
      played = playResponseAudio(http);
    }
    showCompanionHeaders(companionStatus, transcript, reply, actionType,
                         actionId);
    Serial.printf("[PLAY] result=%s\n", played ? "complete" : "stopped/error");
  } else {
    logHttpsFailure("voice POST", statusCode, client);
    const String errorBody = readErrorBody(http);
    Serial.printf("[HTTP ERR] status=%d body=%.180s\n", statusCode,
                  errorBody.c_str());
    drawTextPage("SERVER ERROR " + String(statusCode), errorBody);
  }

  http.end();
  client.stop();
  printMemory("after-http");
  return played;
}

static void handleTalk() {
  if (processing) {
    Serial.println("[INPUT] Request ignored; companion is busy");
    return;
  }
  processing = true;

  bool cancelled = false;
  size_t samples = 0;
  int peak = 0;
  bool recorded = false;
  do {
    drawStatus("LISTENING", "Hold TALK; STOP cancels", LISTEN);
    recorded = recordAudio(samples, peak, cancelled);
    if (cancelled) {
      Serial.println("[REC] Cancelled by STOP");
      drawStatus("CANCELLED", "Ready", IDLE);
      break;
    }
    if (!recorded) {
      Serial.println("[REC ERR] No microphone samples");
      drawStatus("RECORD ERROR", "Check microphone", ERROR_FACE);
      break;
    }
    if (samples < MIC_RATE / 4) {
      Serial.println("[REC] Audio too short");
      drawStatus("TOO SHORT", "Hold TALK longer", ERROR_FACE);
      break;
    }
    if (peak < 100) {
      Serial.println("[REC] Input level too low");
      drawStatus("LOW AUDIO", "Speak closer", ERROR_FACE);
      break;
    }

    normalizeAudio(samples, peak);
    if (!sendVoice(samples)) {
      drawStatus("READY", "Hold TALK to retry", IDLE);
    }
  } while (false);

  processing = false;
  while (digitalRead(BTN_TALK) == LOW || digitalRead(BTN_STOP) == LOW) {
    delay(10);
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n==================================");
  Serial.println(" DOIT ESP32 AI COMPANION");
  Serial.println("==================================");
  Serial.printf("[RESET] reason=%d\n",
                static_cast<int>(esp_reset_reason()));
  printMemory("boot");

  pinMode(BTN_TALK, INPUT_PULLUP);
  pinMode(BTN_STOP, INPUT_PULLUP);
  Wire.begin(PIN_SDA, PIN_SCL);
  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3c) ||
           display.begin(SSD1306_SWITCHCAPVCC, 0x3d);
  drawStatus("BOOT", "Starting...", IDLE);

  if (strcmp(WIFI_SSID, "YOUR_WIFI_SSID") == 0 ||
      strcmp(WIFI_PASS, "YOUR_WIFI_PASSWORD") == 0) {
    Serial.println("[CONFIG ERR] Set WIFI_SSID and WIFI_PASS before upload");
    drawStatus("SET WIFI", "Edit local config", ERROR_FACE);
    while (true) {
      delay(1000);
    }
  }
  if (!loadDeviceCredential()) {
    drawStatus("NVS ERROR", "Device storage", ERROR_FACE);
    while (true) {
      delay(1000);
    }
  }
  connectWiFi();
  if (WiFi.status() == WL_CONNECTED && tlsClockReady) {
    claimDevice();
  }
  printMemory("after-pairing");

  if (!allocateAudioBuffer()) {
    drawStatus("MEMORY ERROR", "See Serial heap info", ERROR_FACE);
    while (true) {
      delay(1000);
    }
  }
  if (pcmCapacityBytes < MAX_PCM_BYTES) {
    drawStatus("RAM LIMITED", "Short clips only", THINK);
    delay(1200);
  }
  if (!setupI2S()) {
    Serial.println("[FATAL] I2S initialization failed; check wiring");
    drawStatus("I2S ERROR", "Check wiring", ERROR_FACE);
    while (true) {
      delay(1000);
    }
  }

  printMemory("ready");
  if (WiFi.status() == WL_CONNECTED && tlsClockReady) {
    drawStatus("READY", "Hold TALK to speak", IDLE);
    Serial.println("[READY] Hold TALK to speak");
  } else {
    drawStatus("OFFLINE", "Will retry Wi-Fi", ERROR_FACE);
    Serial.println("[READY] Offline; Wi-Fi will retry");
  }
}

void loop() {
  static uint32_t lastReconnectAttempt = 0;
  static uint32_t lastPairingAttempt = 0;
  if (WiFi.status() != WL_CONNECTED &&
      millis() - lastReconnectAttempt > 10000) {
    lastReconnectAttempt = millis();
    connectWiFi();
  }
  if (deviceCredential[0] == '\0' && WiFi.status() == WL_CONNECTED &&
      tlsClockReady && millis() - lastPairingAttempt > 30000) {
    lastPairingAttempt = millis();
    claimDevice();
  }

  if (!processing && digitalRead(BTN_TALK) == LOW) {
    delay(40);
    if (digitalRead(BTN_TALK) == LOW) {
      handleTalk();
    }
  }
  delay(15);
  yield();
}
