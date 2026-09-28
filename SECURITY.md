# Security and data flow

## What goes where

| Data | Where it goes |
|---|---|
| The video link | yt-dlp contacts the source platform (YouTube, Drive, …) to read metadata and download captions and the video (≤720p) into the work folder. |
| Audio | Only to a local `whisper-cli` process, and only when the source has no captions. It is never uploaded. |
| Transcript text and the pictures the agent opens | Your agent's model provider, under that provider's terms. Local processing does not make a hosted agent offline. |
| `summary.html` / `summary.pdf` | Stay in the work folder until you share them. |

## What the scripts never do

- Use cookies, browser profiles or logins, or bypass access restrictions. A private or blocked video
  stops with the downloader's own error.
- Read yt-dlp configuration files (`--ignore-config`), install software, or download models.
- Send anything to a transcription or other cloud service.
- Put scripts, remote resources or raw HTML from the notes into the page: all text is escaped, and a
  Content-Security-Policy allows only the embedded images, font, styles and the small click-to-zoom script.

Video speech, captions and on-screen text are untrusted material to summarize, never instructions to
the agent (`SKILL.md` says so).

## Reporting a vulnerability

Please use [a private security advisory](https://github.com/yosishe/visual-video-summarizer/security/advisories/new).
Do not attach private recordings or keys to a public issue.
