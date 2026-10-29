---
name: analyze-video
description: Use when a local video or public YouTube video needs analysis for fact-checking — a diarized, timestamped transcript, the factual claims made (spoken or shown on screen), and the charts, graphics, illustrations and animations tied to each claim, with frame-accurate onset timecodes. Works in Codex and Claude Code; the output feeds a fact-checking step.
---

# Analyze a video for fact-checking

Gemini's agentic video understanding watches the video and returns the transcript, the claims and the visuals
tied to them. Code then pins each visual's onset to an exact frame and saves a still of it. This skill
observes and records; it does not judge whether claims are true. That is a fact-checker's job.

## Setup

None to run by hand. The first run creates a Python environment in `~/.cache/video-analysis/venv-<hash>`
(`VIDEO_ANALYSIS_HOME` moves it) and installs the plugin's pinned dependencies, which needs Python 3.10+ on
PATH and network access to PyPI; later runs reuse it. Needs `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the
environment, and `ffmpeg`/`ffprobe` on PATH for frame snapping (without them, timecodes stay Gemini's
estimates). Never put the key in commands, files or session artifacts. If the key is missing, tell the user to
export it in `~/.zshrc`; Codex runs commands in a snapshot of that shell, so the key exported there is the one used.

In Codex, the default sandbox blocks the network and writes outside the workspace, and every subcommand except
`status` needs both (PyPI on first run, Gemini always). Run the script with escalated permissions
(`sandbox_permissions: "require_escalated"`) from the start instead of letting it fail first, with a
justification such as "uploads the video to Google Gemini for analysis and installs dependencies on first run".
If the user declines, stop and tell them the analysis needs it.

## Run

Resolve `scripts/analyze_video.py` relative to this SKILL.md's directory and run it with `python3` using an
absolute path; it switches to the shared virtual environment itself. Choose a durable absolute session root
and a new descriptive session name; reuse both for follow-ups. Global options precede the subcommand.

```bash
python3 '<skill-dir>/scripts/analyze_video.py' --session-root '<sessions-dir>' \
  analyze '/abs/path/video.mp4' --session aquifers-v1 --fps 30
```

- Source: an absolute local video path, or a public YouTube watch/share/shorts/embed URL (sent to Google directly; no download).
- `--fps`: frame rate (1–240) for the timecodes (`HH:MM:SS:FF`, non-drop). Default: the video's own rate (30 for YouTube).
- `--focus`: narrow the inspection (e.g. "the statistics in the second half"). Omit for everything.
- `--depth deep|lite` (default `lite`). Before every analysis, tell the user which depth you are running. If the
  user is analyzing the video to check its factuality, propose `deep` and warn it costs about 5x more; run `lite`
  unless they accept. Never change depth silently.
  - `deep`: `gemini-3.8-flash`, thinking `medium`. Best visuals and frame-accurate timecodes.
    36 s test clip: 27 visuals, all 18 labels, every onset snapped; ~$0.21 per minute of video, ~3 min.
  - `lite`: `gemini-3.5-flash-lite`, thinking `high`. About 5x cheaper; good enough for claims and on-screen text.
    Same clip: 20 visuals, all 18 labels, 15/20 snapped; ~$0.039 per minute of video, ~1 min. Misses non-text elements (diagrams,
    fills, arrows as separate items); `box_2d` is less accurate, so some timecodes are off by up to ~1 s.
  - `--model` / `--thinking-level` (`minimal`/`low`/`medium`/`high`) override the depth's value. Lite's own
    default (`minimal`) lumps whole scenes together; keep `medium` or `high` for agentic work.
- `refine --session NAME --prompt '...'` re-inspects the video and publishes a new revision; `resume` collects
  a timed-out analysis without resubmitting; `status` shows the session; `cleanup` deletes the remote copies only.

## Output

The command prints the revision's `analysis.md` path. Beside it:

- `analysis.json` — `metadata` (model, source, `usage`) and `analysis`, which holds `transcript` (speaker, start/end seconds, verbatim text), `claims` (id, verbatim quote,
  speaker, `spoken`/`on_screen`, times), `visuals` (id, `claim_ids`, kind, description, verbatim
  `on_screen_text`, `box_2d`, `onset_seconds`, `onset_tc`, `end_seconds`, `frame_path`, `timing_source`,
  `gemini_onset_seconds`, `frame_seconds` — when the still was taken, once the visual is fully shown, so a
  little after the onset — and `timing_confidence` — Gemini's own `low`/`medium`/`high` rating of its timing,
  set before any frame snap; trust `timing_source` over it), `uncertainties` (Gemini's own doubts about the
  video), and `processing_notes` (what code did to the result: stills skipped, a frame snap that failed, a
  time range corrected; empty when nothing happened).
- `transcript.srt` — the diarized transcript, ready for fact-checking.
- `frames/visual-NNN.png` — a still of each visual once fully shown, numbered in `visuals` order and linked by `frame_path` (local videos only).

`timing_source` tells you how far to trust a timecode. `ffmpeg` means the onset was found on the exact frame
where the visual's box starts changing. `gemini` means it is Gemini's estimate: always the case for YouTube,
and for local videos when the box showed no clean onset. In testing on one 36-second motion-graphics clip,
Gemini's onsets fell on a 1/8 s grid and were 0–4 frames off at 30 fps. The ffmpeg snap hit the exact frame on
6 of 7 hand-checked visuals; the seventh, a slow fade on a compressed copy, was 2 frames late.

## Deliver the result

Present the claims with their linked visuals and timecodes, then the uncertainties. Report any
`processing_notes` separately, as notes about frames and timecodes, not as doubts about the claims; when one
says the local video changed, suggest a new session with the right file. Quote claims and on-screen
text verbatim. Say which timecodes are `gemini` estimates. Hand `transcript.srt` (and, when visuals matter,
`analysis.json`) to the fact-checker. Report `usage.total_cached_tokens` from `analysis.json` metadata, or
**unknown**. Agentic mode loads frames repeatedly: a 36-second test clip used 146k–446k tokens per analysis, so warn
before analyzing long videos. Model output is evidence to check, not ground truth.

## Optional: HTML report

When the user wants to browse the result, offer a page: each claim with its visuals, the boxed still,
timecodes and the Gemini-vs-ffmpeg difference, then the transcript, uncertainties and processing notes. The page is generated
by code from `analysis.json`, never written by you, so every run looks the same. Don't hand-edit it.

```bash
python3 '<skill-dir>/scripts/report_html.py' '<revision-dir>' --serve
```

`<revision-dir>` is the folder holding the printed `analysis.md`. The command writes `report.html` there, prints
its path, then prints a `http://127.0.0.1:PORT/report.html` link and keeps serving until stopped, so run it in
the background (Claude Code: a background command; Codex: escalated, as for analysis, since the sandbox blocks
opening a port). Give the user the link to open in their browser or the app's browser pane. Without `--serve`
it only writes the file. Stop the server when the user is done.

