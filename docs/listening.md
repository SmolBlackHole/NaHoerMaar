# Listening together

Parent: [Documentation index](README.md)

NaHörMaar lets a Discord friend group choose music in a shared dashboard while
the bot plays audio in a voice channel. Closing the browser does not stop the
music. The bot currently has **one queue and one active voice connection across
all its servers**; separate sessions per server are [planned](../ROADMAP.md).

## Get into the room

Sign in to the dashboard with a Discord account on the owner's whitelist. The
owner adds Discord user IDs in the access configuration; signing in alone does
not grant control. Pick an available server and voice channel in the sidebar, or
join a voice channel and call `/pspsps` in Discord. The command also checks the
whitelist. Everyone in the current channel hears the same audio.

The dashboard shows the current track and upcoming queue. Browser video, when
available, is muted because the audio plays in Discord. Artwork and video load
only after browser consent; declining that consent does not stop Discord audio.

## Pick what plays next

Search YouTube Music for songs or select Videos when you want video results.
You can also paste a supported YouTube song, video or playlist link. A playlist
opens as a preview so you can choose its tracks before adding them. Search and
playlist results can refresh in the background without changing a selection you
are making.

Every queued occurrence has its own position, even when the same song was added
twice. The queue shows who added it. People on the whitelist can reorder or
remove upcoming entries, undo a recent removal, or clear upcoming tracks.
Recently played keeps a short history with play counts and lets you add a track
again. Clearing upcoming tracks leaves the current song playing.

## Control playback and radio

Pause keeps the current position. Stop returns the current song to the front of
the queue so the next Play starts it from the beginning. Skip advances to the
next entry. Seek, volume and optional crossfade are available in the player.

Radio starts from a song or playlist and adds related tracks automatically.
Manually queued songs keep priority. Ending radio stops new automatic additions;
tracks it already added remain in the queue. The [architecture guide](architecture.md#catalog-metadata-and-radio)
explains the refill policy.

The bot saves the queue and playback position. After a backend restart it rejoins
the saved channel and restores the track, volume and pause state. Radio does not
restart automatically. If a source cannot be played, the dashboard reports the
problem and playback moves on. Current verification and remaining live checks
are listed in [testing and acceptance](testing.md#live-acceptance).
