# dictate

Local, private push-to-talk dictation for Windows. Hold a key, speak, release: your words appear
at the cursor in any app. Speech recognition runs on your own GPU/CPU; nothing leaves the machine.

**Status: Milestone 3** (streaming, live preview and incremental cleanup). Vocabulary and history (M4)
and a tray app (M5) are next. Design: [local-dictation-build-prompt.md](local-dictation-build-prompt.md).

## Setup (once)

Needs Windows 10/11, Python 3.12 and [uv](https://docs.astral.sh/uv/). An NVIDIA GPU is optional.

Run these in Command Prompt or PowerShell, from the project folder:

1. Install dependencies (no PyTorch): `uv sync`
2. Download models (~5.5 GB: speech recognition, the llama.cpp runtime and the cleanup model). This is
   the only command that uses the network: `uv run dictate setup`
3. Mic check (prints what you say instead of typing it): `uv run dictate run --dry-run`

## Use

Start dictate and leave the window open (Ctrl+C quits):

```
uv run dictate
```

| Do this | To |
|---|---|
| Hold **Right Ctrl**, speak, release | insert text at the cursor |
| Double-tap **Right Ctrl** … tap again | hands-free dictation |
| **Esc** while recording | cancel |
| Hold **Shift** while releasing | raw text (skips AI cleanup for that utterance) |
| Say "new line" / "new paragraph" | line breaks (when said as a phrase of their own) |
| Say "scratch that" | drop the previous sentence, or undo the previous insert |
| Say "snake case user id", "camel case get user", "dash dash verbose" | `user_id`, `getUser`, `--verbose` |

## While you speak

- **Live preview.** A small overlay at the bottom of the screen shows what you're saying as you say it.
  Settled words are bright; the still-changing end is dimmed. It never takes keyboard focus and clicks
  pass through it. After you release it shows "Cleaning…", then the inserted text, then fades out.
  You can turn it off, move it to the top, or resize it in `[preview]`.
- **Streaming.** dictate cuts your speech at natural pauses (`segment_pause_ms`) and transcribes each
  finished phrase while you keep talking. On release, only the last phrase is left to transcribe.
- **Incremental cleanup.** Finished sentences are cleaned while you keep talking. A sentence is
  cleaned early only when it's safe to: never when the next phrase starts with a correction ("No wait,
  Wednesday"), continues the sentence ("…and then"), or is part of a list ("First… Second…"). Long
  dictations then only wait for the last sentence or two.
- **Skipping the model.** Text that is already clean and has no numbers, symbols, fillers or
  corrections is formatted by rules alone (`skip_when_clean`), which saves 100–700 ms.

## What the AI cleanup does

A small local model (Gemma-4-E2B, 4-bit, via llama.cpp on your GPU) edits the transcript. It never
answers or acts on what you said:

- It removes fillers (um, uh, like, you know), stutters and false starts.
- It applies self-corrections: "Tuesday, no wait, Wednesday" becomes "Wednesday".
- It fixes punctuation and capitalization, and turns spoken "first… second… third…" into numbered lists.
- It writes numbers, times, dates, money, percentages, units, versions and phone numbers as digits
  and symbols ("five thirty pm" becomes "5:30 PM", "twelve percent" becomes "12%").
- It writes emails, URLs, handles and file names normally ("john dot smith at example dot com"
  becomes "john.smith@example.com").
- It writes dictated math with symbols ("three times four is twelve" becomes "3 × 4 = 12").
  Set `math_style = "ascii"` to get `x^2` and `<=` instead.
- It spells software, products, companies and people correctly. A built-in list fixes the casing of
  well-known names such as GitHub, PostgreSQL, iPhone, Next.js and pgvector.

Guardrails check every output. If the model answers a question, follows a dictated instruction, invents
words, drops your request, or takes longer than `timeout_ms`, dictate inserts the rule-formatted
transcript instead. Use `dictate models` to list the cleanup models and `dictate bench cleanup` to compare them.

Other commands: `dictate lang es` / `fr` / `en` (switch language; takes effect on the next
utterance), `dictate devices` (choose a mic), `dictate config --edit` (all settings, with comments).

The settings file lives at `%APPDATA%\dictate\config.toml`. Formatting, replacements, commands,
vocabulary and insert settings reload without a restart.

## Privacy

- After `setup`, the app refuses every network connection that isn't to localhost (a Python-level
  guard, enforced by a test that runs the real models with the network blocked). Acceptance check:
  turn Wi-Fi off, and dictation still works.
- Audio is never saved or logged. Transcripts are logged only if you set `log_transcripts = true`.
- Pasted text is flagged so Windows clipboard history (Win+V) and cloud clipboard skip it, and your
  previous clipboard (text, images, rich text) is restored afterwards.

## Benchmark clips (your voice)

- Guided recording of 36 short prompts (28 en, 4 es, 4 fr), about 10 minutes. Run it again to
  continue where you left off: `uv run dictate bench record`
- Record only some of them: `uv run dictate bench record --only es fr`
- Measure WER and latency per variant (writes `bench/reports/*.md`): `uv run dictate bench asr`
- Compare cleanup models on 97 test cases (`bench/cleanup/cases.jsonl`) and on your clips end to end:
  `uv run dictate bench cleanup`. This also checks incremental against single-pass cleanup on
  multi-sentence dictations (`bench/cleanup/segmented.jsonl`).
- Release-to-text latency per stage, with the live preview on vs off and streaming on vs off:
  `uv run dictate bench e2e`
- Live preview quality: lag, flicker, and streamed vs whole-utterance accuracy: `uv run dictate bench streaming`

Benchmarks need the GPU to themselves. Close other GPU work first (games, or ML jobs in WSL/Docker,
which show up as `vmwp.exe` in Task Manager).

Clips are saved to `bench/asr/clips/` and are git-ignored, so they stay on this machine.

## Measured (RTX 4060 + Ryzen 7 7700X)

### Cleanup models (`dictate bench cleanup`, 97 cases)

| model | exact | assistant behaviour (before guardrails) | leaked into output | cleanup p50 | VRAM |
|---|---|---|---|---|---|
| **Gemma-4-E2B QAT (default)** | **96%** | 2% | **0** | **156 ms** | 1.5 GB |
| Qwen3.5-4B | 81% | 1% | 0 | 393 ms | 3.0 GB |
| Qwen3.5-2B | 73% | 1% | 0 | 200 ms | 1.4 GB |

On your recorded English number clips, cleanup takes the word error rate from 29% to 0% ("five thirty pm"
becomes "5:30 PM"). Gemma's prompt prefix is fully cached: 20 new prompt tokens per call. Qwen3.5's hybrid
architecture re-processes about 500 tokens per call.

### Release to text, full pipeline (M3, `dictate bench e2e`)

Release to inserted text, with the live preview on and AI cleanup on. Your clips are replayed in real
time; pasting adds about 11 ms.

| utterance | M3 (streaming + incremental cleanup + n-gram drafting) | M2 (one pass at release) |
|---|---|---|
| 2.4 s | **124 ms** | 261 ms |
| 8 s | **246 ms** | 437 ms (7 s) |
| 17.8 s, no pauses | **416 ms** | — |
| 24 s | **351 ms** | 1,050 ms |
| 59 s, multi-paragraph | **64 ms** | 1,254 ms (same code, streaming off) |

Where the time went:

- Speech recognition at release is near zero, because finished phrases are transcribed while you
  speak. Only speech with no pauses at all (the 17.8 s clip) leaves work for release, about 160 ms.
- Cleanup: n-gram speculative decoding (`[llm] speculative = "ngram"`) drafts tokens by copying from
  the transcript. That makes cleanup about 3× faster (468 → 154 ms median on long transcripts), with
  the same accuracy on the test sets.
- Incremental cleanup: on the 59 s dictation, 4 of 5 chunks were cleaned before release, and the last
  one only needed rules.
- The live preview adds no measurable time at release.

### Live preview (`dictate bench streaming`)

| metric | result | target |
|---|---|---|
| word spoken → shown, median / 90th percentile | 0.09 s / 0.51 s | < 1 s |
| settled words later revised (flicker) | 0.3% (1 of 370) | ~0 |
| word error rate, streamed vs whole-recording | 3.1% vs 7.2% | streamed close to whole |

Cutting at pauses is *more* accurate than transcribing a long recording in one go, because Parakeet
degrades on long audio.

Benchmarks only mean something when the GPU is otherwise idle. `bench e2e` waits for a quiet GPU before
each run and flags any run it couldn't get one for. A GPU job in WSL made earlier runs 10–40× slower;
dictate is equally slow in daily use while such a job runs.

### Speech recognition only (raw mode, M1)

Release-to-text latency (finalize, VAD, ASR and formatting; pasting adds ~11 ms):

| ASR config | 3 s utterance | 15 s utterance | VRAM |
|---|---|---|---|
| Parakeet v3 fp32, CUDA (default) | 119 ms | 271 ms | ~3.1 GB |
| Parakeet v3 int8, CPU | 138 ms | 427 ms | 0 |

Notes:
- 62 ms of each number is the deliberate release tail (`audio.release_tail_ms`), which catches your last syllable.
- On CUDA, cuDNN `HEURISTIC` conv search is essential: the default falls back to a path that is about 12× slower.
- The GPU drops to a low-power state after about 1 s idle. Keep-warm (`asr.keep_warm`) holds it up while you speak.
- The 15 s case still misses the 250 ms raw target. M3 fixes this by transcribing finished segments while you're still talking.

## Development

- Tests (unit tests plus one integration test that loads the real models offline): `uv run pytest`
- Lint: `uv run ruff check src tests`
- Pipeline timings on a 16 kHz WAV: `uv run dictate transcribe some.wav`

Windows notes: apps running as administrator reject keystrokes from a normal process. In that case
the text is left on your clipboard (press Ctrl+V), or run dictate as administrator.
