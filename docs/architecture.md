# Planned architecture

Parent: [Project README](../README.md)

The Nuxt dashboard will provide login, voice channel selection, a queue and
playback controls through a FastAPI API. All controls will live in the dashboard;
no Discord chat or slash commands are planned.

The Python backend will use `yt-dlp` to resolve YouTube audio, FFmpeg to process
it and `discord.py` to play it in Discord voice channels.

See the [roadmap](../ROADMAP.md) for upcoming work.
