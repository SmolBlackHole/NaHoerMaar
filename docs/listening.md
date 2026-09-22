# Listening together

Parent: [Documentation index](README.md)

NaHörMaar gives a Discord friend group one place to choose music. The dashboard
shows the queue and controls the bot; the bot sends the shared audio to a voice
channel. This guide covers what listeners can do. For now, the bot has one queue
and one active voice connection across all servers it has joined.

## Contents

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
channel from the sidebar. If you're already in a regular voice channel, type
`/pspsps` in Discord to call the bot over instead. The command checks the same
whitelist without requiring a dashboard login. Its reply is visible only to
you; a successful call gets `:3`. Calling it in the bot's current channel
leaves playback alone. If you call it to another channel, interrupted playback
resumes there, or the next queued track starts when nothing was playing. A
track you explicitly paused remains paused.

Friends in the selected voice channel hear the same audio. Other people with
access can open the dashboard, see the same queue and make requests. Closing a
browser tab does not stop the music. The owner can find whitelist and Discord
setup in [development](development.md#configure-discord).

## Your profile and browser

Your display name, avatar and appearance settings belong to your signed-in
NaHörMaar account, so they follow you between devices. You can change them from
your profile. Signing out also signs out other tabs using that session; it does
not stop the bot or clear the queue. At first sign-in, an old browser profile
may suggest a name and avatar. Earlier queue entries do not change owners when
you accept that suggestion.

The **Cookies** action in the sidebar opens the browser's YouTube consent
settings. Artwork and video previews load only after that consent. Discord
audio keeps playing if you decline it. Browser video volume is a separate local
setting, described with the [playback controls](#control-what-is-playing).

## Find a song or open a playlist

Search uses YouTube Music by default. Change the source to Videos for ordinary
YouTube results, or paste a supported song, video or playlist link. Results open
in a separate panel instead of replacing the queue; on a small screen, close the
panel to return to it. A playlist opens as a preview: choose the tracks you
want, optionally skip duplicates, then add that selection to the queue. Opening
a playlist does not add every song by itself.

Known search and playlist results can appear immediately while NaHörMaar checks
for newer ones. If fresh results arrive, the panel offers to show them. It does
not reshuffle the list while you're choosing tracks, and new playlist entries
do not silently join your selection.

## Shape the queue

Each request has its own place, even if someone added the same song twice. You
can see who added it, drag it to a new place or use **Move to position** from its
menu. That menu can also remove a single upcoming entry.

The queue's **Remove** menu offers three scopes: your upcoming tracks, tracks
added by a particular person, or everyone's upcoming tracks. It shows a
confirmation before a bulk removal. Clearing the upcoming queue leaves the
current song playing. A removal can be undone for 12 seconds; the toast names
what was removed and who did it.

**Recently played** shows tracks once playback has been confirmed, including the
current song. It also shows play counts. You can add a song from there again
without searching for it. The history is separate from the upcoming queue:
clearing upcoming tracks does not erase it.

## Control what is playing

Pause keeps the current position. Skip moves to the next entry. Stop puts the
current song back at the front of the queue; the next Play starts it from the
beginning. You can seek on the progress bar, and hovering over it shows the
time you would jump to.

The speaker button opens the audio settings. **Discord bot volume** changes
what everyone in the voice channel hears. **Browser video volume** affects only
the video on your device and starts at 0%; on desktop its slider is also visible
beside the speaker. The optional video does not make your browser the source of
the shared audio.

Crossfade is optional in the same audio settings. When enabled, the bot overlaps
two tracks at a natural transition. The duration can be set from three to seven
seconds. It does not turn a manual Skip into a fade.

## Let Radio find the next few tracks

Start Radio from a track or playlist when you want the bot to keep finding
related music. It adds a few upcoming tracks as the queue runs down. Requests
added by people take priority, so Radio fills the gaps instead of taking over
their choices.

You can end Radio without stopping the current song. The tracks it already
added remain in the queue; ending Radio only stops further additions. Pausing
playback suspends new additions. The [architecture guide](architecture.md#catalog-metadata-and-radio)
explains how the source provider and queue work together.

## When the bot disconnects or restarts

The queue and playback position are saved. After a backend restart, the bot is
designed to rejoin its last channel and continue the current track, or remain
paused if it was paused. An active Radio resumes finding tracks from the same
source. The current song stays visible in Recently played after its audio has
started. An explicit Leave keeps the bot out of the channel until someone joins
it again.

The [live listening checks](testing.md#live-acceptance) show what has actually
been heard and what still needs testing. If a source cannot be played, the
dashboard reports the problem and playback moves on.
