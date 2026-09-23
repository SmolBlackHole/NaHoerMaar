# Set up Discord

Parent: [Documentation index](README.md)

Your NaHörMaar instance needs its own Discord application for two jobs: its bot
plays music in a voice channel, and Discord sign-in identifies the people using
your dashboard. You also need to manage the server where you will install it.
This does not involve inviting the original project's bot.

## Table of contents

- [Set up Discord](#set-up-discord)
  - [Table of contents](#table-of-contents)
  - [Create the application and collect its credentials](#create-the-application-and-collect-its-credentials)
  - [Install the bot on a server](#install-the-bot-on-a-server)
  - [Set the dashboard sign-in redirect](#set-the-dashboard-sign-in-redirect)
  - [Allow people to use it](#allow-people-to-use-it)
  - [Try the connection](#try-the-connection)

## Create the application and collect its credentials

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and create an application for your instance, or open your existing one.
2. Under **General Information**, copy the **Application ID**. This becomes
   `DISCORD_CLIENT_ID`.
3. Under **Bot**, generate or reset the bot token and put it in `DISCORD_TOKEN`.
4. Under **OAuth2**, copy the client secret into `DISCORD_CLIENT_SECRET`. The
   client secret and bot token are different credentials.

Copy [`.env.example`](../.env.example) to `.env` in the repository root and set
those three values. Keep the token and secret in your local environment, not in
Git. Discord's [bot setup guide](https://docs.discord.com/developers/quick-start/getting-started)
shows the current portal screens if their labels move.

NaHörMaar connects through Discord's Gateway. You do not need to configure an
**Interactions Endpoint URL** in the portal. The bot uses the server and voice
state intents in code; it does not need Message Content or Server Members intent
for the current features.

## Install the bot on a server

Keep **Public Bot** off on the **Bot** page if only you should be able to install
your application. Under **Installation**, enable **Guild Install**. You do not
need a public install link. On the **OAuth2** page, use the **OAuth2 URL
Generator**: select the `bot` and `applications.commands` scopes, then choose
**View Channels**, **Connect** and **Speak** under bot permissions. Copy the
generated URL and open it while signed in as the application owner. Choose
**Add to server** and select your server. Your Discord account needs permission
to manage that server. Check any channel-level permission overrides too: the
bot must be able to enter and speak in the voice channel you select.

Discord's [installation guide](https://docs.discord.com/developers/quick-start/getting-started#step-1-creating-an-app)
explains Guild Install. NaHörMaar discovers the servers and voice channels it
can see; there is no guild ID to copy into `.env`. **Public Bot** controls who
can install this application. The whitelist below controls who can use your
running instance after you have installed it; your friends need not own the app.

## Set the dashboard sign-in redirect

For local development, keep this value in `.env`:

```dotenv
PUBLIC_ORIGIN=http://localhost:3012
```

On the application's **OAuth2** page, add this exact URL under **Redirects** and
save it:

```text
http://localhost:3012/api/auth/discord/callback
```

Open the dashboard at `http://localhost:3012` too. `localhost` and
`127.0.0.1`, different ports, and `http` and `https` are different origins. If
you change `PUBLIC_ORIGIN`, register the new origin plus
`/api/auth/discord/callback` in the portal and give the Nuxt server the same
value. Non-local origins require HTTPS. An “invalid OAuth2 redirect_uri” error
usually means the redirect sent by the app does not match the one registered in
Discord. Dashboard sign-in requests the `identify` scope itself; it is separate
from installing the bot on a server. See [Discord's OAuth2 documentation](https://docs.discord.com/developers/topics/oauth2)
for the distinction.

## Allow people to use it

Discord user IDs, rather than names, make up the whitelist. In the desktop
Discord app, open **User Settings** (the gear at the bottom left), then
**Advanced**, and enable **Developer Mode**. Right-click a user in a server,
group chat or DM and choose **Copy User ID**. On mobile, enable Developer Mode
under **Settings > Advanced**, open a user's profile, then use the three-dot
menu to copy their ID. Discord documents both paths in its
[User ID guide](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID).

Copy [`access.example.toml`](../access.example.toml) to `access.toml` and add
the IDs as quoted strings:

```toml
discord_ids = ["123456789012345678", "234567890123456789"]
admin_ids = ["123456789012345678"]
```

`discord_ids` controls dashboard access, playback, queue edits and `/pspsps`.
Anyone on that list can change the shared queue, including entries added by
other people. `admin_ids` must be a subset of `discord_ids`; it grants
access to the live Logs page, not a user-management screen. The file is read
again for access checks, so whitelist edits do not need a backend restart.
Keep `access.toml` out of Git. If it is missing or invalid, access is blocked.

## Try the connection

Start the services as described in [Development](development.md#start-the-services).
Sign in with an allowed Discord account. The dashboard should list the bot's
available servers and voice channels; select one to connect. An allowed user
who is already in a voice channel can also type `/pspsps` in that server to call
the bot over. The backend registers this command globally when it logs into
Discord. If the command does not appear, check that the bot is online, the
installation includes `applications.commands`, and the backend log reports
successful command registration. Discord may need a client refresh before a
newly registered command appears.

[Listening together](listening.md) covers the controls after setup.
