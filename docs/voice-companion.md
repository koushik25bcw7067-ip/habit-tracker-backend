# Voice companion

## Phase 1: laptop workflow

The AI Companion page and the floating companion panel available across the
signed-in website record audio only after the user presses **Record voice
command** or **Talk to me**. Press **Stop recording** to submit it; recordings
stop automatically at 15 seconds. The browser converts the recording to mono,
16-bit PCM WAV at 16 kHz, with microphone noise reduction enabled where
supported. The backend uses the configured Gemini API key for a dedicated audio
transcription pass, then interprets that transcript and selects an intent with
Gemini. Spoken replies use Gemini by default or Azure Speech when
`TTS_PROVIDER=azure` is configured. Azure Speech returns RIFF WAV audio at
24 kHz mono PCM for both the browser and ESP32. The workflow preserves Telugu
speech in Telugu script and mixed Telugu-English speech without translating
it. Provider credentials are never sent to the browser.

Use a secure browser origin for microphone access: `http://localhost` during
local development or HTTPS for a deployed frontend. Sign in first and start
the FastAPI backend. The floating companion can be opened from any signed-in
page, or use the full **AI Companion** page. The frontend API
base is selected through `VITE_API_BASE_URL`; do not put provider credentials
in a frontend variable.

### Laptop voice endpoint

`POST /companion/web/voice/turn`

Required request headers:

```http
Authorization: Bearer <existing-user-access-token>
Content-Type: audio/wav
X-Voice-Session: <43-to-128-character-random-session-id>
```

The body must be mono 16-bit PCM WAV at 16 kHz, no longer than 15 seconds, and
at most 512 KiB. The endpoint uses the signed-in user's existing account and
existing PostgreSQL task, habit, completion, and statistics records.

The response body is always WAV audio. If server-side speech synthesis fails,
the backend returns a short silent fallback WAV so clients can still read the
reply and status headers. The following response headers are percent-encoded
UTF-8:

- `X-Companion-Transcript`: recognized text
- `X-Companion-Reply`: assistant response
- `X-Companion-Status`: `replied`, `clarification`, `confirmation_required`,
  `completed`, or `cancelled`
- `X-Companion-Action-Id`: short-lived pending action identifier, when present
- `X-Companion-Action-Type`: action type after a completed write

Writes are never executed on the first request. The assistant summarizes the
proposed change and asks for a direct spoken yes/no. Pending actions expire
after one minute. Task deletion requires the same confirmation. A clarification
is requested if a task or habit cannot be uniquely identified.

Supported voice intents:

- Create, update, complete, or delete a task
- Create, update, or complete a recurring routine (stored using the existing habit data model)
- Read tasks or active routines
- Read the existing seven-day progress summary
- Ask a conversational question through the existing AI service

The task router and voice processor share the same task service. Habit writes
reuse the existing habit service, including scheduling, category validation,
duplicate-completion checks, and achievement evaluation.

### Companion API and website refresh

- `GET /companion/changes` requires the existing user Bearer token and returns
  the latest completed companion action timestamp and type.
- Active website tabs refresh task, habit, and dashboard data after a local
  voice action. Tabs also check `/companion/changes` every three seconds while
  visible when a physical device is paired.
- `POST /companion/voice/turn` uses the separately paired device Bearer
  credential, accepts the same WAV format, and returns the same audio/text
  response contract.
- `POST /companion/pairing-codes` requires the user's existing Bearer token.
- `POST /companion/claim` accepts a short-lived pairing code and a
  device-generated random credential. The credential is stored only as a
  SHA-256 hash and is returned to neither the website nor device-list API.
- `GET /companion/devices` and `DELETE /companion/devices/{device_id}` require
  the user's existing Bearer token.

The physical ESP32 firmware is in `esp32_ai_companion.ino` and uses the
existing DOIT ESP32 DevKit wiring in the project configuration. Generate a
pairing code in Profile, set it as `PAIRING_CODE` in the sketch, and upload it.
The device generates and saves its own credential in NVS and then uses the
paired `/companion/voice/turn` route. Clear the pairing code from the sketch
after it reports successful pairing; do not copy a website password or API
token into the firmware.

AI chat history is exposed through `GET /ai/conversations`,
`POST /ai/conversations`, `GET /ai/conversations/{id}/messages`, and
`DELETE /ai/conversations/{id}`. The optional `conversation_id` on
`POST /ai/chat` makes successful turns persist to that user-owned thread.
Browser voice and paired-device turns are saved to their own threads; the
voice response includes `X-Companion-Conversation-Id` so the web UI can open
the correct thread.

### Database and setup

Migration `0005_ai_conversations` adds the conversation and message tables and
follows `0004_companion_devices`. It does not alter existing task, habit,
completion, or user schemas. Before deployment, verify the production
database's Alembic revision and create a backup, then apply migrations with
`alembic upgrade head`.

Gemini credentials (`AI_API_KEY`) are used for transcription and intent
interpretation. Set `TTS_PROVIDER=azure`,
`AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, and `AZURE_SPEECH_VOICE` to use
Azure Speech for spoken replies; the Azure region must match the key's
resource region. The F0 tier has a monthly character allowance, not unlimited
usage. Do not expose provider keys in frontend environment variables. If
server-side speech synthesis is unavailable, the laptop UI uses browser speech
as a fallback.

## Manual laptop test checklist

1. Start the backend and frontend locally, sign in, and open **AI Companion**.
2. Allow microphone permission, record a short read-only request such as
   "What are my tasks?", stop recording, and check the transcript and
   spoken reply.
3. Ask for active routines and weekly progress; verify the answers against the
   existing Routines and Statistics pages.
4. Say "Create a task to review notes tomorrow." Confirm the proposed title
   and deadline, then say yes. Verify the new task in the Tasks page.
5. Ask to move that task's deadline. Confirm, then verify the changed date on
   the Tasks page and, when applicable, the Dashboard.
6. Ask to complete a routine scheduled for today. Confirm, then verify the
   completion and weekly progress in the website.
7. Ask to delete a test task. Say no first and verify it remains; repeat and
   say yes to verify it is removed.
8. Test a vague target, a task/habit that is not owned by the account, an
   expired confirmation, denied microphone permission, and unavailable AI/TTS
   configuration. The assistant should clarify or show an error rather than
   claim success.

Automated backend tests use a disposable SQLite database and mocked Gemini
responses. They do not connect to the existing PostgreSQL database or verify
live provider access. Complete the manual checklist before treating Phase 1 as
live end-to-end verified.
