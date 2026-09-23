# Hosting considerations

Parent: [Documentation index](README.md)

NaHörMaar can run on an always-on home server or a VPS. We have not yet
benchmarked a production deployment on either, so these are requirements and
things to measure, not a minimum server specification.

## Table of contents

- [Hosting considerations](#hosting-considerations)
  - [Table of contents](#table-of-contents)
  - [What the host runs](#what-the-host-runs)
  - [Home server or Raspberry Pi](#home-server-or-raspberry-pi)
  - [VPS](#vps)
  - [Check capacity before choosing](#check-capacity-before-choosing)

## What the host runs

One Python backend owns the bot, the shared player and its SQLite database.
It must stay at one worker: another worker would start another bot instance.
The Nuxt server serves the dashboard and forwards its API calls to the backend.
The host also needs Node.js for the music provider, FFmpeg for audio and the
system Opus library on Linux. Start with the versions in
[Development](development.md#set-up).

Keep the [database](engine/database.md) on persistent storage and follow the
tested [backup and restore procedure](recovery.md). Run the dashboard behind HTTPS with
a stable public origin if friends need access from outside your home.
Set `PUBLIC_ORIGIN` to that origin and register its exact OAuth callback in the
[Discord Developer Portal](discord-setup.md#set-the-dashboard-sign-in-redirect).
Keep FastAPI on loopback; expose the Nuxt dashboard through a reverse proxy.
Discord's bot connection itself does not require an incoming web port, but
remote browsers must be able to reach the dashboard's sign-in callback.

## Home server or Raspberry Pi

A 64-bit Raspberry Pi home server is a reasonable candidate to test before
paying for a VPS. The project's `imageio-ffmpeg` version has a
[Linux ARM64 wheel with FFmpeg](https://pypi.org/project/imageio-ffmpeg/), and
`FFMPEG_PATH` can point to a system binary instead. Install the system Opus
library as well. For sustained playback, use reliable storage and cooling, and
check the actual audio path on your Pi. A package installing successfully does
not prove smooth playback or crossfade on that device.

Running at home also makes your internet connection and power supply part of
the service. If friends need the browser UI from elsewhere, give it a stable
HTTPS address and decide how you will expose only that UI. The bot can continue
to use Discord while the browser UI is private to your network, but friends
outside it will not be able to use the dashboard.

## VPS

Both [Hetzner Cloud](https://docs.hetzner.com/cloud/servers/faq/) and
[Hostinger VPS](https://www.hostinger.com/vps/linux-hosting) offer Linux hosts
if running one at home is inconvenient. Choose a plan after measuring playback,
search and crossfade under your own workload. Shared CPU can be enough for light
use, but concurrent audio decoding and transcoding may need more predictable CPU
time. We do not have evidence yet for a particular provider, size or monthly price.

## Check capacity before choosing

Measure memory and per-core CPU while idle, during track start and search, and
through a crossfade. Note whether playback stalls or skips at the same time.
Measure the production Nuxt server separately from the backend, and leave room
for transient FFmpeg processes. The live listening procedure is in
[Testing](testing.md#live-acceptance). A quiet development process is only an
idle baseline, not a hosting benchmark.
