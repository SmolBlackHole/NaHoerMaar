# NaHörMaar

Music for a Discord voice channel, picked together.

NaHörMaar is a small social player for friend groups. Everyone uses the web
dashboard to find songs and shape one shared queue; the bot plays the audio in
Discord. The music keeps going when you close the dashboard.

## How it works

```text
Friends in the dashboard -> shared queue -> bot -> Discord voice channel
```

Search YouTube Music, choose Videos or paste a supported link. Preview a
playlist before adding tracks. The queue shows who added each song, and changes
appear for everyone. Radio can keep finding music while tracks added by people
take priority. Recently played makes it easy to bring a song back.

Discord sign-in and the owner's whitelist control access. A whitelisted listener
can call `/pspsps` to bring the bot into their voice channel. The optional
browser video is muted; everyone hears the audio in Discord. See
[Listening together](docs/listening.md) for controls and queue behavior.

There is currently **one queue and one active voice connection across the bot's
servers**. NaHörMaar is still in development. Separate server sessions, saved
playlists, reactions and listening recaps are [planned](ROADMAP.md), and the
current [Discord listening acceptance](docs/testing.md#live-acceptance) is not
yet complete.

## Try it locally

You need Python 3.12+ and Node.js 24.11+ with npm. From the repository root:

```powershell
python scripts/dev.py setup
```

Then follow the [development guide](docs/development.md) to configure Discord
sign-in, start the backend and dashboard, and run the checks. The backend and
Nuxt dashboard use the same [engine API](docs/engine-api.md).

## Choose your next step

| I want to...                                  | Start here                                |
| --------------------------------------------- | ----------------------------------------- |
| Listen with friends                           | [Listening together](docs/listening.md)   |
| Run or change the project                     | [Development guide](docs/development.md)  |
| Understand the data flow and responsibilities | [Architecture](docs/architecture.md)      |
| Use the HTTP/SSE contract                     | [Engine API](docs/engine-api.md)          |
| See checks and remaining live tests           | [Testing and acceptance](docs/testing.md) |
| Find all guides                               | [Documentation index](docs/README.md)     |
| See future work                               | [Roadmap](ROADMAP.md)                     |

[Contributing](CONTRIBUTING.md) and the [security policy](SECURITY.md) cover
changes and vulnerability reports.

## License

Copyright 2026 SmolBlackHole. Licensed under the
[Mozilla Public License 2.0](LICENSE).
