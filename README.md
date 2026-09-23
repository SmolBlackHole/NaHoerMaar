# NaHörMaar

Music for a Discord voice channel, picked together.

NaHörMaar is a small social player for friend groups. Friends find songs and
shape a shared queue in the browser; a Discord bot plays the audio in the voice
channel. You can close the dashboard and the music keeps going. The queue shows
who added each track, so choosing what plays next stays a group thing.

Search starts with YouTube Music. You can also look for videos, paste a song or
playlist link, or start Radio when nobody wants to pick the next track. The
dashboard has artwork, an optional local video and the controls for the bot.
The [listening guide](docs/listening.md) explains what each person can do and
how the shared controls behave.

We built it because queuing a song through chat commands meant switching
between YouTube and Discord, remembering the right command and sometimes
getting the wrong track anyway. The browser puts discovery, the queue and
playback controls in one place while the sound stays in the voice channel.

This began with our group, but the code isn't tied to our server. Other friend
groups can host their own instance with their own Discord application, Python
backend and Nuxt dashboard. We don't run a public NaHörMaar bot for others to
invite.

It isn't a beginner project or a one-click install. I'm sorry if you were hoping
for that. The [development guide](docs/development.md) explains how to set up
the bot and dashboard, and how to run the backend locally. If you want the bot
available around the clock, run the backend on a VPS or another always-on host.

If you get stuck setting up your own instance, open an issue and we'll try to
help you get it running.

## The name

NaHörMaar combines the German "Na, hör mal" meme with the Dutch word `maar`
("but"). We were learning Dutch together, and the mash-up stuck.

## Try it locally

You need Python 3.12+ and Node.js 24.11+ with npm. From the repository root:

```powershell
python scripts/dev.py setup
```

Then follow [development](docs/development.md) to configure Discord sign-in and
access, start the backend and dashboard, and invite the bot. A listener
can use the web UI without learning Discord bot commands. `/pspsps` is there
when someone wants to call the bot into their voice channel.

## Where it stands

NaHörMaar is in development. The bot has **one shared queue and one
active voice connection across its servers**. It does not give each server an
independent listening room yet. Saved playlists, reactions and listening recaps
are also [future work](ROADMAP.md). The [Discord listening acceptance](docs/testing.md#live-acceptance)
still has open cases; automated checks alone do not settle what listeners hear.

## Find your way

| I want to... | Start here |
| --- | --- |
| Listen with friends | [Listening together](docs/listening.md) |
| Set up or change the services | [Development](docs/development.md) |
| Follow a request through the code | [Architecture](docs/architecture.md) |
| Use the HTTP and event contract | [Engine API](docs/engine-api.md) |
| Check behavior and open listening tests | [Testing and acceptance](docs/testing.md) |
| Browse every guide | [Documentation index](docs/README.md) |
| See planned work | [Roadmap](ROADMAP.md) |

[Contributing](CONTRIBUTING.md) and the [security policy](SECURITY.md) cover
changes and vulnerability reports.

## License

Copyright 2026 SmolBlackHole. Licensed under the
[Mozilla Public License 2.0](LICENSE).
