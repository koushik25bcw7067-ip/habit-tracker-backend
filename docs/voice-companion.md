# Voice companion

## Phase 1: laptop workflow

The AI Companion page and the floating companion panel available across the
signed-in website record audio only after the user presses **Record voice
command** or **Talk to me**. Press **Stop recording** to submit it; recordings
stop automatically at 15 seconds. The browser converts the recording to mono,
16-bit PCM WAV at 16 kHz, with microphone noise reduction enabled where
supported. When `OPENAI_API_KEY` is set on the backend, the recording is sent
to OpenAI Whisper for transcription, and Gemini interprets the transcript and
selects an intent. Without that key, the backend logs a warning and uses the
legacy Gemini audio transcription path. Provider credentials are never sent
to the browser.

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

When speech synthesis succeeds, the response body is WAV audio. The following
response headers are percent-encoded UTF-8:

- `X-Companion-Transcript`: recognized text
- `X-Companion-Reply`: assistant response
- `X-Companion-Status`: `replied`, `clarification`, `confirmation_required`,
  `completed`, or `cancelled`
- `X-Companion-Action-Id`: short-lived pending action identifier, when present
- `X-Companion-Action-Type`: action type after a completed write

If server-side speech synthesis is unavailable, the response is JSON with
`speech_available: false`, `reply`, `transcript`, `status`, and any
`action_type`. The laptop UI falls back to browser speech synthesis where
available and still displays the text.

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

The physical device must use a reachable HTTPS backend base URL supplied at
provisioning time. Firmware, ESP32 pin mappings, and hardware configuration
are intentionally not part of Phase 1.

### Database and setup

Migration `0004_companion_devices` adds one companion/session table. It does
not alter the existing task, habit, completion, or user schemas. Do not run the
migration against the existing PostgreSQL database until its current Alembic
revision and backup have been checked and migration approval is given. No
migration or deployment is performed by this implementation.

Set `OPENAI_API_KEY` in the backend environment to enable Whisper
transcription. Gemini credentials (`AI_API_KEY`) are still used to interpret
the transcript and synthesize replies. Do not expose either key in frontend
environment variables. If speech synthesis is unavailable, the laptop UI uses
browser speech as a fallback.

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

Automated backend tests use a disposable SQLite database and mocked Whisper
and Gemini responses. They do not connect to the existing PostgreSQL database
or verify live provider access. Complete the manual checklist before treating
Phase 1 as live end-to-end verified.
