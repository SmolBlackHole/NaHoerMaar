# Listening together

Parent: [Documentation index](README.md)

NaHörMaar gives a Discord friend group one place to choose music. The dashboard
shows the queue and controls the bot; the bot sends shared audio to a voice
channel. This guide covers what listeners can do. The bot has one queue
and one active voice connection across all servers where it is installed.

## Table of contents

- [Listening together](#listening-together)
  - [Table of contents](#table-of-contents)
  - [Get into the room](#get-into-the-room)
  - [Your profile and browser](#your-profile-and-browser)
  - [Find a song or open a playlist](#find-a-song-or-open-a-playlist)
  - [Shape the queue](#shape-the-queue)
  - [Control what is playing](#control-what-is-playing)
  - [Let Radio find the next few tracks](#let-radio-find-the-next-few-tracks)
  - [When the bot disconnects or restarts](#when-the-bot-disconnects-or-restarts)

## Get into the room

Sign in to the dashboard with a Discord account on the owner's whitelist.
Signing in alone does not grant control. Pick an available server and voice
channel from the sidebar. If you are already in a regular voice channel, type
`/pspsps` in Discord to call the bot over instead. The command checks the same
whitelist without requiring a dashboard login. Its reply is visible only to you;
a successful call gets `:3`.

Calling the bot in its current channel leaves an active or paused track alone.
If the bot is connected but stopped with a non-empty queue, the join command can
start the first queued track. Calling it to another channel resumes interrupted
playback there, or starts the next queued track when nothing was playing. A track
you explicitly paused remains paused.

Friends in the selected voice channel hear the same audio. Other people with
access can open the dashboard, see the same queue and make requests. Closing a
browser tab does not stop the music. The instance owner follows
[Set up Discord](discord-setup.md) to install the bot and manage access.

## Your profile and browser

Your display name, avatar and appearance settings belong to your signed-in
NaHörMaar account, so they follow you between devices. You can change them from
your profile. Signing out also signs out other tabs using that session; it does
not stop the bot or clear the queue. At first sign-in, an old browser profile may
suggest a name and avatar. Earlier queue entries keep the contributor snapshot
recorded when they were added.

The **Cookies** action in the sidebar opens the browser's YouTube consent
settings. Artwork and video previews load only after that consent. Discord audio
keeps playing if you decline it. Browser video volume is a separate local setting,
described with the [playback controls](#control-what-is-playing).

## Find a song or open a playlist

Search uses YouTube Music by default. Change the source to Videos for ordinary
YouTube results, or paste a supported song, video or playlist link. Results open
in a separate panel instead of replacing the queue; on a small screen, close the
panel to return to it. A playlist opens as a preview: choose the tracks you want,
optionally skip duplicates, then add that selection. Opening a playlist does not
queue every track by itself.

Known search and playlist results can appear immediately while NaHörMaar checks
for newer ones. If fresh results arrive, the panel offers to show them. It does
not reshuffle the list while you choose tracks, and new playlist entries do not
silently join the selection. [Catalog and metadata](engine/catalog.md) explains
provider translation, stable result versions and refresh behavior.

## Shape the queue

Each request has its own place, even if someone added the same song twice. You
can see who added it, drag it to a new place or use **Move to position** from its
menu. That menu can also remove one upcoming entry.

The queue's **Remove** menu offers three scopes: your upcoming tracks, tracks
added by a particular person or everyone's upcoming tracks. It asks for
confirmation before a bulk removal. Clearing the upcoming queue leaves the
current song playing. A removal can be undone briefly; the toast names what was
removed and who did it. [Queue and history](engine/queue.md#queue-mutations) owns
the exact deadline and restoration behavior.

**Recently played** shows tracks after playback has been confirmed, including the
current song. It also shows play counts. You can add a song from there again
without searching. History is separate from the upcoming queue: clearing the
queue does not erase it. The underlying identities are documented in
[Queue and history](engine/queue.md).

## Control what is playing

Pause keeps the current position. Skip moves to the next entry. Stop puts the
current song back at the front of the queue; the next Play starts it from the
beginning. You can seek on the progress bar, and hovering shows the time you
would jump to.

The speaker button opens the audio settings. **Discord bot volume** changes what
everyone in the voice channel hears. **Browser video volume** affects only the
video on your device and starts at 0%; on desktop its slider is also visible
beside the speaker. The optional video does not make the browser the source of
shared audio.

Crossfade is optional in the same settings. When enabled, the bot overlaps
natural track transitions for the selected duration. It does not turn a manual
Skip into a fade. The FSM and audio path are documented in
[Playback](engine/playback.md).

## Let Radio find the next few tracks

Start Radio from a track or playlist when you want the bot to keep finding
related music. It adds a few upcoming tracks as the queue runs down. Requests
added by people take priority, so Radio fills gaps instead of taking over their
choices.

You can end Radio without stopping the current song. Tracks it already added
stay in the queue; ending Radio stops only future additions. Pausing playback
suspends new refills. [Radio](engine/radio.md) owns the strategy, provider seed,
refill and restart rules.

## When the bot disconnects or restarts

The queue and playback checkpoint are durable. After a backend restart, the bot
rejoins its saved channel and continues the current track near its last measured
position, or stays paused when it was paused. An active Radio restores its seed
and queued candidates. Explicit Leave clears automatic rejoin intent.

If a saved channel is no longer available, the bot keeps the interrupted track
and position for a later manual join. If a source cannot be played, the dashboard
reports the problem and playback moves on. [Playback](engine/playback.md) owns the
technical restart and reconnect rules; [live listening checks](testing.md#live-acceptance)
separate automated evidence from what listeners have heard in Discord.
