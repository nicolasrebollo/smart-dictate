# Dictate: quick start

Hold a key, speak, let go: clean, punctuated text appears where your cursor is, in any app.

Dictate needs somewhere to turn your speech into text. When you first open it, it asks which you want:

- **A PC that shares Dictate with me.** Someone (maybe you) runs Dictate on a PC with a graphics card
  and gave you a **link** (`https://name.something.ts.net/?t=…`) and a Tailscale invite. Free. Your
  voice only goes to that PC.
- **This computer's graphics card** (Windows with an NVIDIA card only; Dictate only offers it if you
  have one). Free and private: nothing leaves your computer. It downloads about 6 GB once.
- **An online AI service**: OpenAI, Anthropic (Claude), Google (Gemini) or xAI (Grok). You need an
  **API key** from that service (an account with a payment method or credit; usually a few cents per
  hour of dictation). Your recordings are sent to that service.

## 1. Install Dictate

### Windows

1. Download **Dictate-Windows-Setup.exe** and open it.
2. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. (Dictate
   isn't from the Microsoft Store, so Windows doesn't recognize it yet.)
3. Click **Install**, then **Finish**. Dictate opens and asks how it should work (step 2).

### Mac (macOS 13 Ventura or newer)

1. Pick the right download. Open the Apple menu  › **About This Mac**:
   - "Chip: Apple M1/M2/M3/M4…": **Dictate-Mac-AppleSilicon.dmg**
   - "Processor: Intel…": **Dictate-Mac-Intel.dmg**
2. Open the downloaded file and drag **Dictate** onto the **Applications** folder.
3. Open **Applications** and double-click **Dictate**. The first time, macOS says it can't check
   the app. Click **Done**, then open **System Settings › Privacy & Security**, scroll down to
   "Dictate was blocked…", click **Open Anyway** and confirm with your password. (On older macOS,
   right-click Dictate in Applications, choose **Open**, then **Open** again.) You only do this once.
4. Dictate asks how it should work (step 2). Afterwards it asks for three permissions: allow the
   **microphone**, then click **Open System Settings** and turn on **Dictate** under
   **Accessibility** and under **Input Monitoring**. If macOS offers to **Quit & Reopen** Dictate,
   click it.

## 2. Set it up

Dictate asks **"Where should your speech be turned into text?"** Pick one and click **Continue**.

### A PC that shares Dictate with me

1. Install Tailscale from https://tailscale.com/download, sign in, accept the invite you were sent,
   and leave it connected. Dictate only works while Tailscale is on.
2. Copy your link, then paste it when Dictate asks and click **Connect**.

### An online AI service

1. Pick the service. If you don't have an API key yet, click **Open the website**, sign in, create an
   API key there and copy it:
   - OpenAI: platform.openai.com › API keys
   - Anthropic (Claude): platform.claude.com › API keys
   - Google (Gemini): aistudio.google.com › Get API key
   - xAI (Grok): console.x.ai › API keys
2. Paste the key and click **Check key**. Dictate checks it with the service and picks the right
   models for you.
3. **If you chose Claude:** Claude works with text, not audio, so Dictate asks for a second service
   (OpenAI, Gemini or Grok) to turn your speech into text, and a key for it. Claude then tidies the
   text up.

Your key is kept in your computer's password store (Windows Credential Manager or the macOS
Keychain), not in a file, and only ever sent to that service.

### This computer's graphics card

Click **Download**. A window shows the progress (10 to 30 minutes, depending on your connection).
Keep the computer on and connected until it says it's done. If it stops (no internet, for example),
open Dictate again and choose the same thing: it picks up where it stopped.

**Then:** Dictate says it's ready. Hold **Right Ctrl** (Windows) or **Right Option ⌥** (Mac),
speak, and let go.

## 3. The phone (only with a PC that shares Dictate)

1. Open the link in **Safari** (iPhone) or **Chrome** (Android). Allow the microphone.
2. Add it to your Home Screen: on iPhone, Share › **Add to Home Screen**; on Android, ⋮ › **Add to Home screen**.
3. Open **Dictate**. Hold the button and speak, or tap to start and tap to stop. Your words go in
   wherever the cursor is in the text box, and you can edit it freely. Everything in the box is
   copied after each dictation, so you can paste it anywhere.

## 4. Using it on a computer

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

## 5. The Dictate menu: turning it off and on, and other settings

Everything is in Dictate's **microphone icon**:

- **Windows:** in the taskbar, bottom right, next to the clock. If you don't see it, click the **^**
  arrow. **Right-click** the icon to open the menu. (To keep it in view, drag it from the ^ panel
  onto the taskbar.)
- **Mac:** in the menu bar, at the top right of the screen. **Click** the icon to open the menu.

| Menu item | What it does |
|---|---|
| (first lines) | what Dictate is doing right now ("Ready: hold Right Ctrl and speak"), and what it uses ("Using: Online: OpenAI") |
| **Turn off Dictate** / **Turn on Dictate** | Off: the hotkey does nothing and Dictate lets go of the microphone and of the connection. Use it when you want your computer's own dictation, a game or another app that uses the same key, or just a break. The icon goes dim. Choose **Turn on Dictate** to bring it back. |
| **Notes…** | opens your notes (see below) |
| **Language** | English, Español, Français |
| **Hotkey** | the key you hold. Windows: Right Ctrl or Right Alt (useful if your keyboard has no Right Ctrl). Mac: Right Option, Right Command or Fn (for Fn, also set System Settings › Keyboard › "Press 🌐 key to" to **Do Nothing**). |
| **Microphone** | which microphone to use. "Same as the system" follows your computer's sound settings. |
| **Sounds** | the small beeps when it starts and stops listening |
| **Start when I log in** | ticked: Dictate starts by itself when you log in (it's on after setup) |
| **Change how Dictate works…** | pick a different option from step 2, paste a new link, or enter a new API key. Dictate restarts with the new setup. |
| **Check for updates…** | looks for a newer version of Dictate and installs it (see step 7) |
| **Advanced settings…** | the settings file, for rare tweaks. You shouldn't need it. |
| **Quit Dictate** | closes Dictate completely until you open it again (Start menu on Windows, Applications on Mac) or until you next log in |

**Using your computer's own dictation instead:** choose **Turn off Dictate** (or **Quit Dictate**),
then use the built-in one: on Windows press **Win + H**; on a Mac use **Edit › Start Dictation**
(or the 🎤 key). Turn Dictate back on from the same menu when you're done.

**Video calls (Zoom, Teams, Meet):** you can leave Dictate on. It only listens while you hold the
hotkey, and other apps can use the microphone at the same time.

**The icon's colors:** white = ready, red = listening, amber = working, dim = turned off, grey with
a red line = can't reach the PC or the online service (open the menu: the first line says why).

## 6. Notes

Choose **Notes…** in the menu to open a notes window: folders on the left, your notes in the middle,
the open note on the right. Notes save as you type, so there's no Save button. Dictation works in
it like anywhere else: click into a note, hold the hotkey and speak.

- **New note:** **+ New note** (or Ctrl+N; ⌘N on a Mac). It goes into the folder you're looking at.
- **Folders:** **+ New folder**. Right-click a folder (Control-click on a Mac) to make a folder
  inside it, rename it or delete it. Drag a note onto a folder to move it there, or use the folder
  name above the note. Drag a folder onto another folder to put it inside.
- **Search:** type in the search box. It finds every word you type, in the title and the text
  (upper/lower case and accents don't matter: "cafe" finds "Café"). The boxes under it narrow the
  search to **Title only** or **Text only**, and to a period: pick one (Today, Last 7 days…) or type
  a date (2026-11-20, 2026-11, 2026, or 2026-11-01 to 2026-11-15). The last box picks which date
  that means: when the note was **Edited** or **Created**. Click **Title** or the date above the
  list to sort by it.
- **Deleted by mistake?** Deleted notes wait in **Recently deleted** for 30 days. Open it, pick
  the note and choose **Restore**.

Your notes stay on this computer, in one file: `%LOCALAPPDATA%\dictate\notes\notes.db` on
Windows, `~/Library/Application Support/dictate/notes/notes.db` on a Mac. No account, nothing
online. To back them up, copy that file (with Notes closed). Uninstalling Dictate keeps it.

## 7. Updating and uninstalling

- **Update (Windows):** choose **Check for updates…** in Dictate's menu, then **Update now**. Dictate
  downloads the new version, closes, updates itself and starts again (about a minute). Or download
  the new **Dictate-Windows-Setup.exe** yourself and run it. Your setup, settings and notes are kept.
  (With your own graphics card, Dictate updates its engine the next time it starts, in about a minute:
  the 6 GB download isn't repeated.)
- **Update (Mac):** choose **Check for updates…** in Dictate's menu, then **Download**: the new
  version goes to your Downloads folder. Quit Dictate from its menu, open the download, drag Dictate
  into Applications and choose **Replace**. macOS then forgets the permissions: in **System Settings › Privacy & Security**, under
  **Accessibility** and **Input Monitoring**, select Dictate, remove it with **–**, then open Dictate
  and allow it again.
- **Uninstall (Windows):** **Settings › Apps › Installed apps › Dictate › Uninstall**. This also
  deletes the 6 GB download of the graphics-card option. API keys stay in Credential Manager
  (search for "Credential Manager" in the Start menu › Windows Credentials › the entries with
  "Dictate" in their name)
  until you remove them.
- **Uninstall (Mac):** open Dictate's menu, untick **Start when I log in**, choose **Quit Dictate**,
  then drag **Dictate** from Applications to the Trash. API keys stay in the **Keychain Access** app
  (search for "Dictate") until you delete them.
- **Stop using an API key:** delete it on the service's website. Dictate can't use it any more.

## If it doesn't work

- **The icon is grey with a red line:** with a PC's link, Tailscale is off on this computer, or the
  other computer is off or asleep; Dictate reconnects by itself when both are back. Online, check
  your internet connection.
- **The menu says "Didn't work: … rejected the API key" or "out of credit":** check your account on
  the service's website (payment method, credit, limits), or choose **Change how Dictate works…**
  and enter a new key.
- **"The link is out of date" / "wrong token":** ask for a new link, then choose **Change how
  Dictate works…** and paste it.
- **Nothing happens when you hold the key:** check the icon isn't dim (choose **Turn on Dictate**).
  On a Mac, check that Dictate is turned on under **Accessibility** and **Input Monitoring** in
  System Settings › Privacy & Security.
- **The text appears in the wrong place:** click where you want it before you start talking.
- **No Right Ctrl key (many newer laptops):** pick **Right Alt** under **Hotkey** in the menu.
- **Mac: "Dictate is already running" or the app won't open from the download:** drag it into
  Applications first, and open it from there.
- **The graphics-card setup didn't finish:** open Dictate again and choose the same option; it
  continues where it stopped. If it keeps failing, choose a different option, and send the file
  `local-setup.log` from `%LOCALAPPDATA%\dictate\logs` to whoever shared Dictate with you.
