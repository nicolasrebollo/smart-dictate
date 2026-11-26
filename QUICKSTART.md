# Dictate: quick start

Hold a key, speak, release: clean, punctuated text appears where your cursor is, in any app.
The speech recognition runs on the computer of the person who shared Dictate with you. Your device
only sends your voice and gets text back. Nothing is saved.

**You'll need from them:** a Tailscale invite, plus either the **server address and token** (for a
computer) or a **link** (for a phone).

## 1. Everyone: Tailscale

Install Tailscale from https://tailscale.com/download (App Store or Play Store on phones). Sign in,
accept the invite, and keep it connected.

## 2a. Phone (iPhone or Android)

1. Open the link you were given in **Safari** (iPhone) or **Chrome** (Android). Allow the microphone.
2. Add it to your Home Screen: on iPhone, Share › **Add to Home Screen**; on Android, ⋮ › **Add to Home screen**.
3. Open **Dictate**. Hold the button and speak (or tap to start and tap to stop). The text is copied:
   paste it anywhere.

## 2b. Windows

1. Install uv. In PowerShell, run the line below, then close and reopen the window:
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Unzip `dictate.zip` and open a terminal in the `dictate` folder. Run `uv sync`.
3. Run `uv run dictate config --edit`. Under `[client]`, set `server` and `token` to what you were given, then save.
4. Run `uv run dictate`. Hold **Right Ctrl**, speak, release.
5. Optional: `uv run dictate autostart on` starts it at login.

## 2c. Mac

1. Install uv. In Terminal, run `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `source $HOME/.local/bin/env`.
2. Run `unzip ~/Downloads/dictate.zip -d ~`, then `cd ~/dictate`, then `uv sync`.
3. Run `uv run dictate config --edit`. Under `[client]`, set `server` and `token`, then save.
4. Run `uv run dictate`. In System Settings › Privacy & Security, turn on **Terminal** under
   **Accessibility** and **Input Monitoring**, and allow the microphone. Quit Terminal (Cmd+Q),
   reopen it, then run `cd ~/dictate` and `uv run dictate` again.
5. Hold **Right Option (⌥)**, speak, release.
6. Optional: `uv run dictate autostart on` starts it at login. macOS will then ask about **Python**:
   turn Python on under Accessibility and Input Monitoring, then log out and back in.

## Using it on a computer

| Do this | To |
|---|---|
| Hold the hotkey, speak, release | type the cleaned text where your cursor is |
| Double-tap the hotkey … tap again | talk hands-free |
| Esc | cancel |
| Hold Shift while releasing | skip the AI cleanup once |
| Say "new line" / "new paragraph" / "scratch that" | line breaks / delete the last sentence |

The menu-bar or tray icon shows the status (white = ready, red = listening, grey = can't connect) and
lets you switch between English, Spanish and French.

## If it doesn't work

- **The icon is grey, or the page says "Offline":** Tailscale is off, or the host computer is asleep or off.
- **"wrong token":** check the `token` line in your settings.
- **Mac: nothing is typed:** turn on Terminal (or Python, if you use autostart) under Accessibility
  and Input Monitoring, then restart Dictate.
