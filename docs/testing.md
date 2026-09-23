# Testing and acceptance

Parent: [Documentation index](README.md)

Local checks, generated audio recordings and listening in Discord establish
different things. Use this page to choose the right check and to see which live
behaviors still need confirmation. Setup and configuration live in the
[development guide](development.md); the playback model behind the diagnostics
lives in [Playback](engine/playback.md).

## Checks that do not touch the live bot

From the repository root:

```powershell
python scripts/dev.py check
```

This runs repository text and Markdown-link checks, Ruff linting and formatting,
strict mypy and Pyright, backend tests, frontend tests, Nuxt type checking and a
production build. `npm run check` checks only the frontend. Shell wrappers are
available as `scripts/check.sh` and `scripts/check.ps1`. GitHub Actions runs the
standard checks on Windows and Linux.

For a clean Linux precheck before pushing, start Docker and run:

```powershell
python scripts/dev.py check-container
```

This builds the repository snapshot with Python 3.12, Node.js 24 and `libopus0`,
then invokes the same complete gate. It catches clean-environment and Linux-only
problems locally, while GitHub Actions remains responsible for the Windows job
and the final hosted-runner result.

Backend tests use a separate working directory and database per test, synthetic
Discord credentials and simulated voice connections. Real Discord login,
external socket connections and the local dev-server ports are blocked. HTTP
integration tests use an in-process app or temporary loopback server. Audio
fixtures are generated locally and never played in Discord. This isolates
application state, not host CPU usage; run resource-heavy checks in an agreed
window without active shared listening.

To run the backend suite alone:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests
```

The database recovery checks create, verify and restore only temporary SQLite
files. Run them separately with:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_recovery.py
```

They cover account data, queue order, Radio, history and the playback checkpoint.
The operational restore command and scheduler examples are documented in
[Back up and restore NaHörMaar](recovery.md).

Six full engine recordings are opt-in. In an agreed resource window:

```powershell
$env:NAHORMAAR_ENGINE_AUDIO_TESTS = "1"
.venv\Scripts\python.exe -m pytest backend/tests/engine/test_playback_pipeline.py --basetemp=tmp/engine-audio-check -p no:cacheprovider
Remove-Item Env:NAHORMAAR_ENGINE_AUDIO_TESTS
```

These use the real Session, SQLAlchemy, FFmpeg/Opus and Discord audio thread.
Only lookup and network transport are replaced; generated tones reach a local
recording. They check overlap, pause/seek, source failure and restart behavior.
They neither log the bot in nor call its live API. Reusing `--basetemp` replaces
previous output, so choose a fresh directory to retain earlier measurements.

## Playback diagnostics

The backend console includes timestamps and the owning process ID.
`engine.runtime.ready` identifies the listening session.
`engine.audio.completed` records attempt ID, measured position and completion
reason. `engine.playback.resolving` and `engine.playback.resolved` show source
lookup time; `engine.playback.effect_dispatched` and `.effect_replaced` show
scheduled and superseded work. `engine.playback.*` also reports failed effects
or checkpoints.
`engine.radio.*` reports failed refill work. Audio-buffer and FFmpeg lifecycle
messages come from the reused technical adapters.

The catalog, provider request and metadata write boundaries emit `.started`,
`.completed`, `.cancelled` or `.failed` with elapsed time. Cache refresh events
identify search, playlist or track work and say whether a new result replaced
the cached version. These entries omit queries, media URLs, headers, tokens and
exception messages. Session action entries include the request ID, outcome and
queue revision; voice and audio entries include connection or attempt IDs.

For a playback cut, compare `ffmpeg.eof` output and expected seconds with
`audio.buffer.failed` buffered seconds. `engine.audio.prepared` records how early
the next source was opened; `engine.audio.transition` records its age when the
fade begins. This distinguishes a stream that failed while waiting from one
that failed during playback. `audio.underrun` and `audio.recovered` measure a
separate, short period of silence while a reader catches up. `ffmpeg.diagnostic`
records the first occurrence of each sanitized error category before EOF.
`audio.buffer.first_frame` measures startup, while `audio.buffer.read_slow` and
`audio.buffer.not_ready` identify a stalled decoder or incomplete prebuffer.
The underrun entry includes how long the reader has waited for its current frame.

Raw media URLs, headers and tokens are excluded from audio diagnostics. Keep
timestamped console output when investigating a cut: history records confirmed
starts and endings, but it cannot describe everything a Discord listener hears.
Do not start a second bot instance for acceptance. The legacy standalone
playback script has been removed.

## Live acceptance

The historical CI baseline is [run 35662755161](https://github.com/SmolBlackHole/NaHoerMaar/actions/runs/35662755161)
for commit `a36e598`: Windows and Linux jobs each passed 527 backend tests
(6 skipped), 121 frontend tests, API type generation, type checks and build.
The six skipped cases are the optional real FFmpeg/Opus recordings above. CI
checks and earlier listening reports do not establish that the current engine
sounds correct in Discord. The following acceptance is still **open**.

Agree with a listener on the specific server and voice channel, two or three
test tracks, whether the bot may be moved or restarted, and a time when music
may be interrupted. Use the one running bot instance. Before changing anything,
record the tested commit and process start time, current channel, track position,
pause/volume/crossfade settings, radio mode, queue occurrence IDs and existing
history count. Confirm that the running process contains the tested code; if it
does not, agree on a restart before testing. Keep existing user queue entries in
place and add only identifiable test entries.

| Check | Confirm | Status |
| --- | --- | --- |
| Transition, crossfade off | One start; no cut or duplicate history | Pending |
| Natural crossfade | Audible overlap; title and clock switch at fade start | Heard; UI timing open |
| Pause, resume and seek | Controls match sound and position; no second play count | Heard and state checked |
| Skip during overlap | One successor; late callback cannot skip again | Pending |
| Radio | Three upcoming; manual entries first; End radio stops refill | Pending |
| Voice reconnect | Track and position survive; `/pspsps` preserves pause intent | Pending |
| Restart | Rejoin with saved playback state | Abrupt restart checked; clean shutdown open |
| Two tabs | Shared state; local navigation; one SSE stream each | State checked; concurrency open |

Partial run on 2026-09-22: the listener heard the seven-second transition from
"Get Lucky" to "We Are Young" and confirmed that playback resumed at the saved
position after an abrupt process restart. A second abrupt restart preserved the
paused track at 3:42; the listener confirmed it remained paused until manually
resumed and then continued at the same point. A seek changed the displayed and
stored position. The play occurrence and history count did not change during
seek, pause or restart. Two browser tabs showed the same queue, and switching
views or collapsing the sidebar did not reset playback. Existing queue entries
were not manually changed. The first restart ended the active radio under the
previous implementation; Radio restoration now has isolated tests but still
needs a coordinated live check.

During both restarts, the dashboard temporarily showed an unavailable login
screen even though the session was valid. The frontend now retains a known
session on a transient transport failure; its focused regression test and type
check pass. This correction was not retested with another live restart. The
remaining pending and partial cases keep overall live acceptance open.

For each case, note what was audible separately from the UI snapshot and backend
logs. A test passes only when both the listener and the state/history evidence
agree. Stop on an unexpected cut, extra advance or wrong queue entry, and capture
the timestamp, track/attempt and queue occurrence IDs before retrying. Do not
repeat skips or restarts blindly. Logs are local investigation artifacts; keep
secrets and media URLs out of shared notes. Afterwards, remove only agreed test
entries and restore reversible settings. Playback and history already consumed
during the test cannot be undone. Mark untested cases pending rather than passed.
