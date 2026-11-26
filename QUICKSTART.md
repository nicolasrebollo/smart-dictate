# Dictate: quick start

Hold a key, speak, let go: clean, punctuated text appears where your cursor is, in any app.
The speech recognition runs on the computer of the person who shared Dictate with you. Your device
only sends your voice and gets text back. Nothing is saved.

**You'll need from them:** a Tailscale invite and a **link** (it looks like
`https://name.something.ts.net/?t=…`). The same link works on your phone and your computer. Keep it
to yourself: anyone with it can use their computer for dictation.

## 1. Tailscale (every device)

Install Tailscale from https://tailscale.com/download (App Store or Play Store on phones). Sign in,
accept the invite, and leave it connected. Dictate only works while Tailscale is on.

## 2. Install Dictate

### Windows

1. Download **Dictate-Windows-Setup.exe** and open it.
2. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. (Dictate
   isn't from the Microsoft Store, so Windows doesn't recognize it yet.)
3. Click **Install**, then **Finish**.
4. Dictate asks for your link. Copy the link, paste it in, and click **Connect**.
5. Hold **Right Ctrl**, speak, and let go.

### Mac

1. Pick the right download. Open the Apple menu  › **About This Mac**:
   - "Chip: Apple M1/M2/M3/M4…": **Dictate-Mac-AppleSilicon.dmg**
   - "Processor: Intel…": **Dictate-Mac-Intel.dmg**
2. Open the downloaded file and drag **Dictate** onto the **Applications** folder.
3. Open **Applications** and double-click **Dictate**. The first time, macOS says it can't check
   the app. Click **Done**, then open **System Settings › Privacy & Security**, scroll down to
   "Dictate was blocked…", click **Open Anyway** and confirm with your password. (On older macOS,
   right-click Dictate in Applications, choose **Open**, then **Open** again.) You only do this once.
4. Dictate asks for your link. Copy the link, paste it in, and click **Connect**.
5. Dictate asks for three permissions. Allow the **microphone**, then click **Open System Settings**
   and turn on **Dictate** under **Accessibility** and under **Input Monitoring**. If macOS offers to
   **Quit & Reopen** Dictate, click it.
6. Hold **Right Option (⌥)**, speak, and let go.

### Phone (iPhone or Android)

1. Open the link in **Safari** (iPhone) or **Chrome** (Android). Allow the microphone.
2. Add it to your Home Screen: on iPhone, Share › **Add to Home Screen**; on Android, ⋮ › **Add to Home screen**.
3. Open **Dictate**. Hold the button and speak, or tap to start and tap to stop. Your words go in
   wherever the cursor is in the text box, and you can edit it freely. Everything in the box is
   copied after each dictation, so you can paste it anywhere.

## 3. Using it on a computer

| Do this | To |
|---|---|
| Hold the hotkey, speak, let go | type the cleaned-up text where your cursor is |
| Double-tap the hotkey … tap it again | talk hands-free |
| Esc while talking | cancel |
| Hold Shift while letting go | skip the AI cleanup once |
| Say "new line" / "new paragraph" | line break / blank line |
| Say "comma", "period", "question mark", "open quote" … "close quote" | that punctuation |
| Say "scratch that" | delete the last sentence |

Click where you want the text before you start talking: it goes wherever the cursor is.

## 4. The Dictate menu: turning it off and on, and other settings

Everything is in Dictate's **microphone icon**:

- **Windows:** in the taskbar, bottom right, next to the clock. If you don't see it, click the **^**
  arrow. **Right-click** the icon to open the menu. (To keep it in view, drag it from the ^ panel
  onto the taskbar.)
- **Mac:** in the menu bar, at the top right of the screen. **Click** the icon to open the menu.

| Menu item | What it does |
|---|---|
| (first line) | what Dictate is doing right now, e.g. "Ready: hold Right Ctrl and speak" |
| **Turn off Dictate** / **Turn on Dictate** | Off: the hotkey does nothing and Dictate lets go of the microphone and of the connection. Use it when you want your computer's own dictation, a game or another app that uses the same key, or just a break. The icon goes dim. Choose **Turn on Dictate** to bring it back. |
| **Language** | English, Español, Français |
| **Hotkey** | the key you hold. Windows: Right Ctrl or Right Alt (useful if your keyboard has no Right Ctrl). Mac: Right Option, Right Command or Fn (for Fn, also set System Settings › Keyboard › "Press 🌐 key to" to **Do Nothing**). |
| **Microphone** | which microphone to use. "Same as the system" follows your computer's sound settings. |
| **Sounds** | the small beeps when it starts and stops listening |
| **Start when I log in** | ticked: Dictate starts by itself when you log in (it's on after setup) |
| **Connect to a different PC…** | paste a new link, e.g. when you were sent a new one |
| **Advanced settings…** | the settings file, for rare tweaks. You shouldn't need it. |
| **Quit Dictate** | closes Dictate completely until you open it again (Start menu on Windows, Applications on Mac) or until you next log in |

**Using your computer's own dictation instead:** choose **Turn off Dictate** (or **Quit Dictate**),
then use the built-in one: on Windows press **Win + H**; on a Mac use **Edit › Start Dictation**
(or the 🎤 key). Turn Dictate back on from the same menu when you're done.

**Video calls (Zoom, Teams, Meet):** you can leave Dictate on. It only listens while you hold the
hotkey, and other apps can use the microphone at the same time.

**The icon's colors:** white = ready, red = listening, amber = working, dim = turned off, grey with
a red line = can't reach the PC (open the menu: the first line says why).

## 5. Updating and uninstalling

- **Update (Windows):** download the new **Dictate-Windows-Setup.exe** and run it. Your link and
  settings are kept.
- **Update (Mac):** quit Dictate from its menu, drag the new Dictate into Applications and choose
  **Replace**. macOS then forgets the permissions: in **System Settings › Privacy & Security**, under
  **Accessibility** and **Input Monitoring**, select Dictate, remove it with **–**, then open Dictate
  and allow it again.
- **Uninstall (Windows):** **Settings › Apps › Installed apps › Dictate › Uninstall**.
- **Uninstall (Mac):** open Dictate's menu, untick **Start when I log in**, choose **Quit Dictate**,
  then drag **Dictate** from Applications to the Trash.

## If it doesn't work

- **The icon is grey with a red line:** Tailscale is off on this device, or the other person's
  computer is off or asleep. Dictate reconnects by itself when both are back.
- **"The link is out of date" / "wrong token":** ask for a new link, then choose **Connect to a
  different PC…** in the menu and paste it.
- **Nothing happens when you hold the key:** check the icon isn't dim (choose **Turn on Dictate**).
  On a Mac, check that Dictate is turned on under **Accessibility** and **Input Monitoring** in
  System Settings › Privacy & Security.
- **The text appears in the wrong place:** click where you want it before you start talking.
- **No Right Ctrl key (many newer laptops):** pick **Right Alt** under **Hotkey** in the menu.
- **Mac: "Dictate is already running" or the app won't open from the download:** drag it into
  Applications first, and open it from there.
