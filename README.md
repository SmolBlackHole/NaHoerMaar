# NaHörMaar

A shared music player for Discord friend groups.

Pick songs together, see who added what and keep listening while you hang out.
The web app is where everyone manages the queue; the bot plays the music in
your Discord voice channel. Closing the dashboard leaves the music running.

## Listening together

- Search YouTube Music, switch to video search or paste a link. Preview playlists
  and choose which tracks to add.
- Share one queue with your friends. See who requested each song, change its
  position and undo removals. Changes appear in everyone's dashboard.
- Start a radio from a song or playlist and let it keep the queue going.
  Tracks added by people take priority.
- Requeue something from Recently played, check play counts and browse the
  Overview. Cover and optional video views follow the current track.
- Sign in with Discord and choose your name, avatar and appearance. Access is
  limited to the owner's whitelist.

Use `/pspsps` in Discord to call the bot into your voice channel. Playback supports
pause, seek and shared volume. Backend restarts restore the saved channel, track
position, volume and pause state without counting another play.

NaHörMaar is still in development. It currently has **one shared queue and one
active voice connection**, even if the bot belongs to several servers. Independent
server sessions, saved playlists, reactions and richer listening recaps are on the
[roadmap](ROADMAP.md).

## Development

Install Python 3.12+ and Node.js 24.11+ with npm. From the repository root, run:

```powershell
python scripts/dev.py setup
python scripts/dev.py check
```

See the [development guide](docs/development.md) to configure Discord and try
playback.

## Documentation

- [Development guide](docs/development.md): setup, Discord configuration and local playback
- [Architecture](docs/architecture.md): state, persistence, integrations and the dashboard
- [API](docs/api.md): controls, authentication and live updates
- [Roadmap](ROADMAP.md): implemented features, open checks and future work
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## License

Copyright 2026 SmolBlackHole. Licensed under the
[Mozilla Public License 2.0](LICENSE).
