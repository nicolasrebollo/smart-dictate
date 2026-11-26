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
3. Restart `uv run dictate pc`. It now prints **your link** (`https://your-pc…ts.net/?t=…`). That one
   link sets up every other device: phones open it, the desktop app asks for it.

## 3. The Mac (and other computers)

Install the **Dictate app** (see [Building the apps](#building-the-apps-and-sharing-them) for where
it comes from): open the `.dmg`, drag Dictate into Applications, open it and paste your link. No
terminal, no models: the download is about 20 MB. [QUICKSTART.md](QUICKSTART.md) walks through it step
by step, including the one-time macOS security prompts. The same goes for a second Windows PC:
run `Dictate-Windows-Setup.exe` and paste the link.

Don't install the app on the PC that runs `dictate pc`: that already includes the hotkey client.
Only one copy can use the hotkey per computer (two would type everything twice), so whichever starts
second says so and stops. The app and a source checkout keep separate start-at-login entries, so
installing or uninstalling the app never changes `dictate autostart`.

The Mac hotkey is **Right Option**.

<details><summary>From source instead (with a terminal)</summary>

1. Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Copy this folder to the Mac, then run `uv sync` in it. That installs the client only, no models.
3. Run `uv run dictate app` and paste your link when it asks (or put `server` and `token` in the
   `[client]` section of `uv run dictate config --edit`, then run `uv run dictate`).
4. Allow the app you ran it from (Terminal) in **System Settings › Privacy & Security** under
   **Microphone**, **Accessibility** and **Input Monitoring**, then run it again.
5. Run `uv run dictate autostart on` to start it at login.
</details>

## 4. The iPhone

1. Make sure Tailscale is on.
2. Open the link the PC printed (`https://your-pc…ts.net/?t=…`) in **Safari**. If you added Dictate to
   your Home Screen before, delete that icon first: it can't pick up a new link.
3. Tap Share › **Add to Home Screen**.

Open **Dictate** from your home screen. Hold the button, speak and let go, or tap to start and tap to
stop. Your words go into the text box **wherever the cursor is**, and you can type, delete or move
around in it between dictations. The text is kept until you clear it. After each dictation, all of it is
**copied automatically**: paste it into any app. There's also a Share button.
(iOS doesn't let an app outside the App Store type into other apps, so copying is the closest it gets.)

## Using it (Mac and PC)

| Do this | To |
|---|---|
| Hold **Right Ctrl** (PC) / **Right Option** (Mac), speak, release | type the cleaned text where your cursor is |
| Double-tap the hotkey … tap again | talk hands-free |
| **Esc** while talking | cancel |
| Hold **Shift** while releasing | skip the AI cleanup once |
| Say "new line" / "next line", "new paragraph" / "next paragraph" | line break / blank line |
| Say "comma", "period", "question mark", "exclamation point", "colon", "semicolon", "dot dot dot" | that punctuation |
| Say "open quote" … "close quote", "open paren" … "close paren" | quotes / parentheses |
| Say "scratch that" | delete the last sentence |

The tray / menu-bar icon shows the status (white = ready, red = listening, amber = working, dim =
turned off, grey with a red line = can't reach the PC). Its menu holds every setting:

| Menu item | What it does |
|---|---|
| **Turn off / Turn on Dictate** | off: the hotkey does nothing, the microphone and the connection are released (use the computer's own dictation, a game that needs the key, a break). On the PC, only this PC's hotkey turns off: the server keeps serving your other devices. |
| **Language** | English, Spanish, French |
| **Hotkey** | Right Ctrl / Right Alt on Windows; Right Option / Right Command / Fn on Mac |
| **Microphone** | which mic, or the system's default |
| **Sounds** | start / stop beeps |
| **Start when I log in** | autostart on or off (Windows: Task Manager › Startup apps; Mac: Login Items) |
| **Connect to a different PC…** | paste a new link (desktop app) |
| **Advanced settings…** | the settings file |
| **Quit Dictate** | stop until you open it again or log in again |

The AI cleanup removes fillers ("um", "like"), applies your corrections ("Tuesday, no wait,
Wednesday" → "Wednesday"), and fixes punctuation. It writes numbers, times, money, emails and URLs
properly ("five thirty pm" → "5:30 PM"), turns "snake case user id" into `user_id`, writes math with
symbols, and spells product and company names correctly. In a message, the greeting ("Hi Maria,") and
the sign-off ("Thanks, Nick") go on their own lines, unless you dictated your own line breaks.
It never answers or acts on what you dictate. If its output looks wrong, the plain transcript is used
instead.

Spoken punctuation works in Spanish and French too ("coma", "punto y aparte", "abre interrogación";
"virgule", "point à la ligne", "point d'interrogation"). A command word you use as a normal word
stays a word: "the trial period", "a new line of products", "the Oxford comma".

To teach it names and jargon, add them to `vocabulary` in the PC's `[server]` settings
(`uv run dictate config --edit` on the PC), for example `vocabulary = ["Okonkwo", "pgvector"]`.
Restart `dictate pc` to apply.

## Building the apps and sharing them

The desktop app is the client only (hotkey, microphone, paste, tray). It's built with PyInstaller:
`Dictate-Windows-Setup.exe` (installs for the current user, no admin rights) and a `.dmg` for each
kind of Mac. A Mac app can only be built on a Mac, so GitHub builds all three for you:

1. Push this repository to GitHub.
2. **Actions › Build apps › Run workflow.** After about 10 minutes the run's **Artifacts** hold
   `Dictate-Windows-Setup.exe`, `Dictate-Mac-AppleSilicon.dmg` and `Dictate-Mac-Intel.dmg`.
3. Or tag a version (`git tag v0.2.0` then `git push --tags`): the same files are attached to a
   GitHub **Release**, which is a nice download page if the repository is public. If it's private,
   send the files another way (a shared Drive folder, for example).

To share Dictate with someone: invite them to your tailnet in the Tailscale admin console (Share
your PC's machine, or invite them as a user), then send them the right file, your link, and
[QUICKSTART.md](QUICKSTART.md).

To build the Windows installer on this PC instead: `uv sync --extra server --group build`, then
`uv run python packaging/build.py`. Install [Inno Setup](https://jrsoftware.org/isinfo.php) for the
installer; without it you get a `.zip` with `Dictate.exe` inside.

The apps aren't signed, so the first open shows a one-time warning ("Windows protected your PC" →
More info › Run anyway; on a Mac, Privacy & Security › Open Anyway). QUICKSTART.md covers both.
Removing the warnings needs an Apple Developer ID (with notarization) and a Windows code-signing
certificate, both paid.

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

- **The icon is grey (can't reach the PC):** check the PC is on and running `dictate pc`, and Tailscale is
  connected on both devices. If the PC's token changed, use **Connect to a different PC…** with the new link.
- **Nothing happens on the Mac:** allow Dictate (or Terminal, from source) under Accessibility and
  Input Monitoring in System Settings › Privacy & Security. Check the icon isn't dim (turned off).
- **Wrong microphone:** pick it under **Microphone** in the tray menu.
- **Logs:** `%LOCALAPPDATA%\dictate\logs\dictate.log` (Windows), `~/Library/Logs/dictate/dictate.log` (Mac).
- **Slow on the PC:** another program is using the GPU (a game, or an ML job in WSL/Docker).

## Development

`uv run pytest` runs the tests. Benchmarks are on the PC: `uv run dictate bench asr` and
`uv run dictate bench cleanup` (record test clips with `dictate bench record`). The design notes are
in [local-dictation-build-prompt.md](local-dictation-build-prompt.md).
