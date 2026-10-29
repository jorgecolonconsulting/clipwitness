# ClipWitness

A plugin marketplace for Claude Code and Codex with one plugin, **video-analysis**. Its `analyze-video` skill
uses Gemini video understanding to watch a video and produce:

- a diarized, timestamped transcript (`transcript.srt`),
- the factual claims made, spoken or shown on screen, quoted verbatim,
- the charts, graphics and animations tied to each claim, with frame-accurate onset timecodes and a still of each.

It records what the video says and shows; it does not judge whether claims are true. Feed its output to a
fact-checker.

## What you get

A `deep` analysis of a 36-second explainer on aquifers, rendered from its `analysis.json`. Each claim is quoted
verbatim and comes with the visuals that show it. Each visual has a timecode snapped to the exact frame by
ffmpeg (Gemini's own estimate is shown beside it), a still with the element boxed, and its on-screen text:

![Deep analysis report: summary, stats, and the first claim with its visuals](docs/images/deep-analysis-overview.png)

`deep` and `lite` read the same claim differently. Deep lists the well, the pump house and each usage icon as
separate elements with their own onsets. Lite lumps them into one entry:

![The same claim analyzed by Deep (five separate visuals, each with its own timecode) and Lite (one lumped entry)](docs/images/granular-vs-lumped.png)

What the extra cost buys: cost per minute of video against visual elements timed correctly, three runs of each
depth on the same clip:

![Cost per minute of video vs. visual elements timed correctly, for Deep, Lite and Lite minimal](docs/images/cost-vs-accuracy.png)

## Requirements (macOS)

- Python 3.10 or newer on `PATH` (`brew install python@3.13`). The system `python3` (3.9) can start the
  skill, but the skill needs a newer Python to build its environment.
- `ffmpeg` (`brew install ffmpeg`), which pins each visual to its exact frame. Without it, timecodes stay
  Gemini's estimates.
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey), exported in the shell that
  starts Claude Code or Codex:

  ```bash
  echo 'export GEMINI_API_KEY="your-key"' >> ~/.zshrc
  ```

  Free-tier keys work for local video files. YouTube URLs need a key from a paid-tier (billing-enabled) project.

There is no separate setup step. On first use the skill creates a Python environment in
`~/.cache/video-analysis/venv-<hash>` and installs its pinned dependencies (about 20 seconds, needs network access
to PyPI). Claude Code and Codex share it. A plugin update with new dependencies builds a new `venv-<hash>` beside
the old one, so an analysis running in the other host keeps working. Delete old `venv-*` folders when you like
(about 90 MB each). Set `VIDEO_ANALYSIS_HOME` to keep them somewhere else.

## Install in Claude Code

To install from a local clone instead, use its path in place of `jorgecolonconsulting/clipwitness`.

**CLI** (also covers the Code tab of the Claude desktop app, which uses the same plugin settings):

```bash
claude plugin marketplace add jorgecolonconsulting/clipwitness
```

```bash
claude plugin install video-analysis@clipwitness
```

Or, inside a session: `/plugin marketplace add jorgecolonconsulting/clipwitness`, then `/plugin install video-analysis@clipwitness`.

**Claude desktop app:**

1. In the sidebar, open **Customize**, then **Plugins**.
2. Click **Add** → **Add marketplace** → **Add from a repository**, enter `jorgecolonconsulting/clipwitness`, and click **Sync**.
3. Find **video-analysis** in the plugin list and click **+** to install it.
4. Use it from the **Code** tab: start a new session, or run `/reload-plugins` in an open one.

The desktop app adds marketplaces only from a Git repository, not from a local folder. For a local clone, use
the CLI commands above. The skill runs Python, ffmpeg and your API key on your Mac, so it only works in Claude
Code (CLI or the desktop Code tab), not in Chat or Cowork.

## Install in Codex

Codex reads the same repository through `.agents/plugins/marketplace.json`.

**CLI:**

```bash
codex plugin marketplace add jorgecolonconsulting/clipwitness
```

```bash
codex plugin add video-analysis@clipwitness
```

Or run `codex`, type `/plugins`, open the **ClipWitness** tab, and install **video-analysis**.

**Codex desktop app:** add the marketplace with the first command above (the app and CLI share
`~/.codex/config.toml`), then restart the app. Open **Plugins**, choose the **ClipWitness** marketplace,
open **video-analysis**, and click the **+** (install) button. Start a new thread to use it.

### What Codex needs that Claude Code doesn't

No config changes. Codex runs shell commands in a sandbox with no network and no write access outside your
workspace, and the skill needs both: Google for every analysis, PyPI and `~/.cache/video-analysis` on first run.
So the skill asks Codex to run it outside the sandbox, and Codex asks you to approve. Approve it, or choose to
always allow that command so later runs don't ask.

Codex's automatic approval reviewer (`approvals_reviewer`), if you use it, may refuse the request because it
uploads a local video to an external service. Say in your prompt that uploading the video to Gemini is OK.

Codex runs commands in a snapshot of your interactive shell, so the key your `~/.zshrc` exports is the one the
skill uses, even if you started Codex with a different one. Keep a single `GEMINI_API_KEY` in `~/.zshrc`. A
mismatch shows up as `Gemini interaction failed (INTERNAL ...)` on every run in Codex only, and `cleanup` then
reports `failed` because the files belong to the other key's project.

## Use it

Ask in plain language. The agent picks the skill, tells you which depth it will run, and reports the claims with
their visuals and timecodes:

> Analyze ~/Movies/aquifers.mp4 for fact-checking.

To name the skill explicitly, use `/video-analysis:analyze-video` in Claude Code. In Codex, type `@` and pick the plugin or its skill.

Depths: `lite` (default, about $0.039 per minute of video on a paid key; a 36-second clip takes about 1 minute) is
good for claims and on-screen text. `deep` (about $0.21 per minute) costs about 5x more and finds more visuals
with better timecodes. The agent asks before running `deep`.

You can also run the script yourself from a clone of this repository:

```bash
python3 plugins/video-analysis/skills/analyze-video/scripts/analyze_video.py \
  --session-root ~/video-sessions analyze ~/Movies/aquifers.mp4 --session aquifers-v1
```

The command prints the path of `analysis.md`. Beside it are `analysis.json`, `transcript.srt` and
`frames/visual-NNN.png`. Follow-ups reuse the session: `refine --session aquifers-v1 --prompt '...'`, `resume`
(collect a timed-out analysis without paying again), `status`, and `cleanup` (delete the copies uploaded to Google).

For a page you can browse (claims with their boxed stills and timecodes, the transcript, the uncertainties),
ask for the HTML report, or run `python3 plugins/video-analysis/skills/analyze-video/scripts/report_html.py <revision-dir> --serve` and open the link it prints.
The page is generated from `analysis.json` by code, so it looks the same every run.

Local videos are uploaded to Google's Files API (deleted after 48 hours, or by `cleanup`). Gemini's output is
evidence to check, not ground truth.

## Troubleshooting

- `Gemini interaction failed (INTERNAL ...)`: a server-side Gemini error, more frequent on free-tier keys. Run
  the same command again with a new session name.
- `Dependency setup failed`: the first run couldn't reach PyPI or write to `~/.cache/video-analysis`. Retry once
  network access is available. In Codex, approve running the command outside the sandbox.
- `Cannot write ~/.cache/video-analysis`: the command ran inside Codex's sandbox. Approve it outside the sandbox.
- `nodename nor servname provided`: no network, same cause in Codex.
- `needs Python 3.10 or newer`: `brew install python@3.13`.

## Layout

```
.claude-plugin/marketplace.json    Claude Code marketplace
.agents/plugins/marketplace.json   Codex marketplace
plugins/video-analysis/
  .claude-plugin/plugin.json       Claude Code manifest
  .codex-plugin/plugin.json        Codex manifest
  requirements.lock                pinned runtime dependencies
  shared-video/                    Gemini client, sessions, environment bootstrap
  skills/analyze-video/            SKILL.md and its scripts
tests/                             pytest suite (no network, no API key)
docs/images/                       README screenshots
```

## Development

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r plugins/video-analysis/requirements.lock -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest tests
```

```bash
claude plugin validate .
```

## License

MIT. See [LICENSE](LICENSE).
