# smart-dictate

Hold a key, speak, let go: clean, punctuated text appears wherever your cursor is (VS Code,
browsers, Outlook, Slack, anything). It works on **Windows, Mac and iPhone**.

Dictate removes filler words, applies your corrections ("Tuesday, no wait, Wednesday" → "Wednesday"),
writes numbers, times, emails and code identifiers properly, and follows spoken commands ("new
paragraph", "comma", "scratch that"). The speech recognition and the AI cleanup run in one of three
places, which you pick when you set up the app:

| | Where your speech is turned into text | You need | Privacy |
|---|---|---|---|
| **Your own GPU** | this Windows PC's NVIDIA graphics card (6 GB or more) | a one-time 6 GB download | nothing leaves the PC |
| **A PC that shares Dictate** | someone's PC with a GPU (yours, or a friend's) | Tailscale and that PC's link | only between your devices, encrypted |
| **An online AI service** | OpenAI, Anthropic (Claude), Google (Gemini) or xAI (Grok) | your own API key (pay per use) | your recordings go to that service |

```
                       ┌─ your GPU (local mode) ──────────────────┐
 hotkey + mic ──►  engine: speech detection, spoken commands, formatting ──► text at your cursor
                       ├─ a PC with a GPU, over Tailscale ────────┤   (speech recognition + AI cleanup)
 iPhone web app ───►   └─ an online service, with your API key ───┘
```

**Just want to use it?** Download the app and follow [QUICKSTART.md](QUICKSTART.md): no terminal needed.

## Get the app

Download **Dictate-Windows-Setup.exe**, **Dictate-Mac-AppleSilicon.dmg** or **Dictate-Mac-Intel.dmg**
from the [Releases](../../releases) page, open it, and Dictate asks how it should work. The apps
aren't signed, so the first open shows a one-time warning ([QUICKSTART.md](QUICKSTART.md) shows how
to get past it). Mac: macOS 13 or newer. "Your own GPU" is Windows only for now.

### An online AI service

Pick a service, paste an API key from its website, done. Dictate checks the key and picks the best
models that key can use: a speech-to-text model and a fast, inexpensive text model for the cleanup.

| Service | Speech to text | Cleanup | Get a key |
|---|---|---|---|
| OpenAI | ✓ (`gpt-transcribe`) | ✓ (a fast GPT model) | [platform.openai.com](https://platform.openai.com/api-keys) |
| Anthropic (Claude) | – (Claude reads text, not audio) | ✓ (`claude-opus-5-5`) | [platform.claude.com](https://platform.claude.com/) |
| Google (Gemini) | ✓ (`gemini-3.5-transcribe`) | ✓ (a Gemini Flash model) | [aistudio.google.com](https://aistudio.google.com/apikey) |
| xAI (Grok) | ✓ (`grok-voice-transcribe-2.0`) | ✓ (a non-reasoning Grok model) | [console.x.ai](https://console.x.ai/) |

Choosing Claude asks for a second service for the speech part (Claude then does the cleanup).
API keys are kept in the system keychain (Windows Credential Manager, macOS Keychain), never in the
settings file, and are only ever sent to their own service. In online mode Dictate connects to the
services you chose and nothing else (a built-in network guard enforces it). The models are saved in
the `[online]` section of the settings file, where you can change them (Claude Haiku 5.5 is faster
and cheaper than Opus, for example: `cleanup_model = "claude-haiku-5-5"`).

### Your own GPU (Windows, NVIDIA)

The first time, Dictate downloads Python, NVIDIA's GPU libraries (pinned to the exact versions in
this repository's `uv.lock`, hash-checked) and the models into `%LOCALAPPDATA%\dictate\app-runtime`
(about 6 GB, 10–30 minutes). After that it works offline. Uninstalling Dictate deletes that folder.

### A PC that shares Dictate (the original setup)

One PC with a GPU runs the server; your other computers and your phone are light clients. Set the
server up from source:

1. On the PC (Windows, NVIDIA GPU, [uv](https://docs.astral.sh/uv/)): `uv sync --extra server`, then
   `uv run dictate setup` (downloads the models, 6 GB), then `uv run dictate pc` (server + hotkey
   client + tray icon). `uv run dictate autostart on` starts it at login.
2. Install [Tailscale](https://tailscale.com/download) on every device with the same account, and run
   `tailscale serve --bg 8765` on the PC.
3. Restart `uv run dictate pc`. It prints **your link** (`https://your-pc…ts.net/?t=…`). That one link
   sets up everything else: the desktop app asks for it, phones open it.

Don't install the app on the PC that runs `dictate pc`: that already includes the hotkey client. Only
one copy can use the hotkey per computer (two would type everything twice), so whichever starts
second says so and stops. The app and a source checkout keep separate start-at-login entries.

To share your PC with friends, invite them to your tailnet (Tailscale admin console › Share your PC's
machine), then send them the app, your link and [QUICKSTART.md](QUICKSTART.md). Everyone uses the
same link; to cut someone off, remove them from your tailnet.

### The iPhone (with a PC that shares Dictate)

Open the PC's link in **Safari**, then Share › **Add to Home Screen**. Hold the button and speak, or
tap to start and tap to stop. Your words go into the text box where the cursor is, and everything in
it is copied after each dictation, so you can paste it anywhere. (If you added Dictate to your Home
Screen before, delete that icon first: it can't pick up a new link.)

## Using it

| Do this | To |
|---|---|
| Hold **Right Ctrl** (Windows) / **Right Option** (Mac), speak, release | type the cleaned text where your cursor is |
| Double-tap the hotkey … tap again | talk hands-free |
| **Esc** while talking | cancel |
| Hold **Shift** while releasing | skip the AI cleanup once |
| Say "new line" / "next line", "new paragraph" / "next paragraph" | line break / blank line |
| Say "comma", "period", "question mark", "exclamation point", "colon", "semicolon", "dot dot dot" | that punctuation |
| Say "open quote" … "close quote", "open paren" … "close paren" | quotes / parentheses |
| Say "scratch that" | delete the last sentence |

The tray / menu-bar icon shows the status (white = ready, red = listening, amber = working, dim =
turned off, grey with a red line = can't reach the speech recognition). Its menu holds every setting:

| Menu item | What it does |
|---|---|
| (first lines) | what Dictate is doing, and where your speech is recognised ("Using: Online: OpenAI") |
| **Turn off / Turn on Dictate** | off: the hotkey does nothing, the microphone and the connection are released (use the computer's own dictation, a game that needs the key, a break) |
| **Notes…** | your notes: folders, search by words, title, text or date (see below) |
| **Language** | English, Spanish, French |
| **Hotkey** | Right Ctrl / Right Alt on Windows; Right Option / Right Command / Fn on Mac |
| **Microphone** | which mic, or the system's default |
| **Sounds** | start / stop beeps |
| **Start when I log in** | autostart on or off (Windows: Task Manager › Startup apps; Mac: Login Items) |
| **Change how Dictate works…** | switch between your GPU, a PC's link and an online service, or enter a new key or link |
| **Advanced settings…** | the settings file |
| **Quit Dictate** | stop until you open it again or log in again |

Spoken punctuation works in Spanish and French too ("coma", "punto y aparte", "abre interrogación";
"virgule", "point à la ligne", "point d'interrogation"). A command word you use as a normal word
stays a word: "the trial period", "a new line of products", "the Oxford comma". The AI cleanup never
answers or acts on what you dictate, and if its output looks wrong the plain transcript is used.

### Notes

**Notes…** in the menu opens a notes window (Windows and Mac): nested folders on the left, the notes
in the middle, the open note on the right. It saves as you type, and you can dictate into it. The
search finds every word in any order, ignoring case and accents, in the title and text or just one
of them, and narrows by when a note was created or last edited (Today, Last 7 days, or a typed
`2026-11-20`, `2026-11`, `2026-11-01 to 2026-11-15`). Drag a note onto a folder to move it. Deleted
notes wait 30 days in **Recently deleted**.

The notes are one SQLite file on the computer, never online: `%LOCALAPPDATA%\dictate\notes\notes.db`
(Windows), `~/Library/Application Support/dictate/notes/notes.db` (Mac). Uninstalling keeps it. The
window runs as its own process (`Dictate --notes`, or `dictate notes` from source), because on macOS
only one thread can draw windows and in the app that thread runs the menu-bar icon.

To teach it names and jargon, add them to `vocabulary` in the `[server]` settings, for example
`vocabulary = ["Okonkwo", "pgvector"]` (on the PC that runs the server, or in the app's own settings
for local and online mode). Online services also get the list as spelling hints.

## Building the apps

The app is built with PyInstaller. It holds the hotkey client and the engine for online mode (speech
detection on the CPU, spoken commands, formatting), but no speech or cleanup models; the Windows app
also carries what local mode needs to set itself up (uv, a Dictate wheel and the pinned
requirements). A Mac app can only be built on a Mac, so GitHub builds all three:

1. **Actions › Build apps › Run workflow.** The run's **Artifacts** hold the three files. Each build is
   checked with `Dictate --self-test` (imports, the bundled speech detector, certificates, a whole
   dictation through the online-mode engine) before it's packaged.
2. Or push a tag (`git tag -a v0.2.0 -m "Dictate 0.2.0"`, then `git push origin v0.2.0`): the files
   are attached to a GitHub **Release**. Bump the version in `pyproject.toml` and
   `src/dictate/__init__.py` first.

To build the Windows app on your own PC (in a separate environment, since the app uses the CPU build
of onnxruntime and the server the GPU build):

```
set UV_PROJECT_ENVIRONMENT=build\appenv
uv sync --no-dev --group build --extra cloud
uv run --no-sync python packaging/build.py
```

Install [Inno Setup](https://jrsoftware.org/isinfo.php) for the installer; without it you get a `.zip`.
Removing the "unknown app" warnings needs an Apple Developer ID (with notarization) and a Windows
code-signing certificate, both paid.

## Speed

With your own GPU (RTX 4060), from releasing the key to the text arriving:

| What you said | Time |
|---|---|
| short sentence (2 s) | ~50 ms |
| sentence with numbers to format (4 s) | ~450 ms |
| paragraph (24 s) | ~400 ms |
| long dictation (1 min, several paragraphs) | ~65 ms (cleaned while you were still talking) |

From another device add one network round trip (+5–20 ms at home, +30–80 ms elsewhere). Online,
each phrase is sent as soon as you pause, so on release only the last one is left; expect roughly
half a second to two seconds, depending on the service and the model.

## Privacy

- **Your own GPU / a PC that shares Dictate:** audio and text only travel between your own devices
  (Tailscale encrypts them). The server listens on the PC itself only (`127.0.0.1`), every connection
  must present the token, and a built-in guard stops the server from connecting to the internet.
- **Online:** your recordings and the text go to the service you chose, under its terms. Dictate
  connects to nothing else.
- Nothing is saved: no audio, no history. Your previous clipboard is restored after each paste.
  (Notes you write in **Notes…** are kept, in a file on your computer only.)

## Troubleshooting

- **The icon is grey:** with a PC's link, check the PC is on and running `dictate pc`, and Tailscale
  is connected on both devices. Online, check your internet connection.
- **"Didn't work: … rejected the API key" or "out of credit"** in the menu: fix it on the service's
  website, or enter a new key with **Change how Dictate works…**.
- **Nothing happens on the Mac:** allow Dictate under Accessibility and Input Monitoring in System
  Settings › Privacy & Security. Check the icon isn't dim (turned off).
- **Wrong microphone:** pick it under **Microphone** in the menu.
- **Logs:** `%LOCALAPPDATA%\dictate\logs` (Windows), `~/Library/Logs/dictate` (Mac). Local mode's
  setup and server log to `local-setup.log` and `local-server.log` there; the Notes window to `notes.log`.
- **Local setup downloads slowly:** the models come from Hugging Face's servers at full speed (many
  parallel pieces per file). A first download can start slowly while their servers fetch the files, then
  speeds up. If it stays at a couple of MB/s, it's usually the connection (Wi-Fi, a VPN, a metered
  network) or antivirus scanning each file as it arrives.

## Development

`uv sync --extra server` (the GPU server) or `uv sync --extra cloud` (online mode only), then
`uv run pytest`. `uv run dictate app` runs the app from source; `uv run dictate client` reads the
mode from the settings file. Benchmarks run on the PC: `uv run dictate bench asr` and
`uv run dictate bench cleanup` (record test clips with `dictate bench record`). The design notes are
in [local-dictation-build-prompt.md](local-dictation-build-prompt.md). Licensed under [MIT](LICENSE).
