# Security

## Reporting a problem

Please don't open a public issue for a security problem. Use **Security › Report a vulnerability**
on this repository (GitHub's private vulnerability reporting) instead, with what you found and how
to reproduce it. You'll get an answer as soon as possible.

Never include your server token, your link (`…/?t=…`) or an API key in a report or an issue. Log
files hide them automatically, but check before you attach anything.

## How Dictate protects you

- **The server** listens on 127.0.0.1 only. Other devices reach it through Tailscale (`tailscale
  serve`, never `funnel`), and every connection must present the server's token (144 random bits,
  compared in constant time). The phone page never passes its address on (no referrer, a strict
  content security policy) and can't be framed by other sites.
- **A network guard** in every process allows connections only to where Dictate needs to go: your
  server, or the online services you chose. The GPU server itself connects to nothing after setup.
- **API keys** for online mode live in the system keychain (Windows Credential Manager, macOS
  Keychain), never in the settings file, and are only sent to their own service.
- **Logs** never contain audio, tokens, links or API keys (they're hidden in every line). Transcripts
  are logged only if you turn `log_transcripts` on.
- **Downloads** are pinned: models by repository revision, llama.cpp and the speech detector by
  SHA-256, local mode's Python packages by version and hash from `uv.lock`.
- **The apps** are built by GitHub Actions from this repository (actions pinned to exact commits,
  least-privilege permissions) and self-tested before release. Each Release lists the SHA-256 of
  every file in `SHA256SUMS.txt`. They aren't code-signed yet, so your system warns on first open.
