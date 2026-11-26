# dictate

Hold a key, speak, let go: clean, punctuated text appears wherever your cursor is (VS Code,
browsers, Outlook, Slack, anything). It works on your **PC, Mac and iPhone**.

The speech recognition and the AI cleanup run on **your PC's GPU**. Your Mac and iPhone are light
clients: they stream your voice to the PC and get text back. They download no models (the client is
about 20 MB). Nothing goes to a cloud service. Tailscale connects your own devices directly, encrypted.

```
 Mac / PC client ──┐                       ┌─ speech recognition (Parakeet, GPU)
 (hotkey + paste)  ├── your voice ──► PC ──┤
 iPhone web app ───┘   ◄── clean text ──   └─ AI cleanup (Gemma, GPU)
```

## 1. The PC (once)

You need Windows, an NVIDIA GPU, Python 3.12 and [uv](https://docs.astral.sh/uv/). In this folder:

1. `uv sync --extra server` installs everything.
2. `uv run dictate setup` downloads the models (about 6 GB, once).
3. `uv run dictate pc` starts the server and the hotkey client, and adds a tray icon.
4. `uv run dictate autostart on` starts it automatically when you log in.

The PC must be on for your Mac and iPhone to work.

## 2. Tailscale (once, so the Mac and iPhone work from anywhere)

1. Install [Tailscale](https://tailscale.com/download) on the PC, the Mac and the iPhone, and sign in
   with the same account on all three.
2. On the PC, run `tailscale serve --bg 8765`. This gives the server an HTTPS address that only your
   devices can reach.
3. Restart `uv run dictate pc`. It now prints the exact settings for your Mac and the link for your iPhone.

## 3. The Mac

1. Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Copy this folder to the Mac, then run `uv sync` in it. That installs the client only, no models.
3. Run `uv run dictate config --edit` and fill in the `[client]` section with what the PC printed:
   ```toml
   server = "wss://your-pc.your-tailnet.ts.net"
   token = "…"
   ```
4. Run `uv run dictate`. The first time, allow the app you ran it from (Terminal) in **System Settings
   › Privacy & Security** under **Microphone**, **Accessibility** and **Input Monitoring**, then run it again.
5. Run `uv run dictate autostart on` to start it at login.

The Mac hotkey is **Right Option**.

## 4. The iPhone

1. Make sure Tailscale is on.
2. Open the link the PC printed (`https://your-pc…ts.net/#t=…`) in **Safari**.
3. Tap Share › **Add to Home Screen**.

Open **Dictate** from your home screen. Hold the button, speak and let go, or tap to start and tap to
stop. The cleaned text is **copied automatically**; paste it into any app. There's also a Share button.
(iOS doesn't let an app outside the App Store type into other apps, so copying is the closest it gets.)

## Using it (Mac and PC)

| Do this | To |
|---|---|
| Hold **Right Ctrl** (PC) / **Right Option** (Mac), speak, release | type the cleaned text where your cursor is |
| Double-tap the hotkey … tap again | talk hands-free |
| **Esc** while talking | cancel |
| Hold **Shift** while releasing | skip the AI cleanup once |
| Say "new line", "new paragraph" or "scratch that" | line breaks / delete the last sentence |
| Tray / menu-bar icon | status (white = ready, red = listening, amber = working, grey = can't reach the PC), language (English, Spanish, French), settings, quit |

The AI cleanup removes fillers ("um", "like"), applies your corrections ("Tuesday, no wait,
Wednesday" → "Wednesday"), and fixes punctuation. It writes numbers, times, money, emails and URLs
properly ("five thirty pm" → "5:30 PM"), turns "snake case user id" into `user_id`, writes math with
symbols, and spells product and company names correctly. It never answers or acts on what you
dictate. If its output looks wrong, the plain transcript is used instead.

To teach it names and jargon, add them to `vocabulary` in the PC's `[server]` settings
(`uv run dictate config --edit` on the PC), for example `vocabulary = ["Okonkwo", "pgvector"]`.
Restart `dictate pc` to apply.

## Speed

Measured on the PC (RTX 4060), from releasing the key to the text arriving:

| What you said | Time |
|---|---|
| short sentence (2 s) | ~50 ms |
| sentence with numbers to format (4 s) | ~450 ms |
| paragraph (24 s) | ~400 ms |
| long dictation (1 min, several paragraphs) | ~65 ms (cleaned while you were still talking) |

Your voice streams to the PC while you speak, so from the Mac or iPhone you add roughly one network
round trip: +5–20 ms at home, +30–80 ms elsewhere.

## Privacy

- Audio and text only travel between your own devices. Tailscale encrypts the connection end to end.
- The server listens on the PC itself only (`127.0.0.1`), so nothing on your Wi-Fi can reach it.
  Tailscale forwards your devices to it, and every connection must present the token.
- The PC never connects to the internet after `dictate setup`. A built-in guard blocks it, and the
  client can only connect to your PC.
- Nothing is saved: no audio, no history. Your previous clipboard is restored after each paste.

## Troubleshooting

- **The icon is grey (can't reach the PC):** check the PC is on and running `dictate pc`, Tailscale is
  connected on both devices, and the `server`/`token` in the Mac settings match what the PC printed.
- **Nothing happens on the Mac:** allow Terminal (or whatever app runs dictate) under Accessibility,
  Input Monitoring and Microphone, then restart dictate.
- **Wrong microphone:** run `uv run dictate devices`, then set `input_device` in `[client]`.
- **Slow on the PC:** another program is using the GPU (a game, or an ML job in WSL/Docker).

## Development

`uv run pytest` runs the tests. Benchmarks are on the PC: `uv run dictate bench asr` and
`uv run dictate bench cleanup` (record test clips with `dictate bench record`). The design notes are
in [local-dictation-build-prompt.md](local-dictation-build-prompt.md).
