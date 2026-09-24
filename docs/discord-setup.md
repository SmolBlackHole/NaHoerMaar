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
4. Still under **Bot**, enable **Server Members Intent**. NaHörMaar uses the
   member directory to show names on the Access page.
5. Under **OAuth2**, copy the client secret into `DISCORD_CLIENT_SECRET`. The
   client secret and bot token are different credentials.

Copy [`.env.example`](../.env.example) to `.env` in the repository root and set
those three values. Keep the token and secret in your local environment, not in
Git. Discord's [bot setup guide](https://docs.discord.com/developers/quick-start/getting-started)
shows the current portal screens if their labels move.

NaHörMaar connects through Discord's Gateway. You do not need to configure an
**Interactions Endpoint URL** in the portal. The bot uses server, voice-state and
server-member intents. It does not use Message Content. Discord treats the
member intent as privileged, so it must be enabled both in the portal and in the
bot code.

## Install the bot on a server

Enable **Public Bot** on the **Bot** page. Discord requires this before an
application can keep a default installation link. Public installation does not
grant access to NaHörMaar: the owner, admins and listeners are still controlled
by the access policy below.

Under **Installation**, disable **User Install** and keep **Guild Install**
enabled. Choose **Discord Provided Link**, then set the Guild Install scopes to
`bot` and `applications.commands`. Give the bot only **View Channels**,
**Connect** and **Speak**. Discord shows these permissions as the integer
`3146752`.

Anyone with permission to manage a Discord server can now use the installation
link to add the bot there. They still cannot sign in to or control this
NaHörMaar instance unless the owner or an admin grants them access. Check
channel-level permission overrides too: the bot must be able to enter and speak
in the voice channel you select.

Discord's [installation guide](https://docs.discord.com/developers/quick-start/getting-started#step-1-creating-an-app)
explains Guild Install. NaHörMaar discovers the servers and voice channels it
can see; there is no guild ID to copy into `.env`. The Discord installation
link controls where the bot may be added. The access policy below controls who
can use your running instance; your friends need not own the app.

## Set the dashboard sign-in redirect

For local development, keep this value in `.env`:

```dotenv
PUBLIC_ORIGIN=http://localhost:3000
```

On the application's **OAuth2** page, add this exact URL under **Redirects** and
save it:

```text
http://localhost:3000/api/auth/discord/callback
```

Open the dashboard at `http://localhost:3000` too. `localhost` and
`127.0.0.1`, different ports, and `http` and `https` are different origins. If
you change `PUBLIC_ORIGIN`, register the new origin plus
`/api/auth/discord/callback` in the portal and give the Nuxt server the same
value. Non-local origins require HTTPS. An “invalid OAuth2 redirect_uri” error
usually means the redirect sent by the app does not match the one registered in
Discord. Dashboard sign-in requests the `identify` scope itself; it is separate
from installing the bot on a server. See [Discord's OAuth2 documentation](https://docs.discord.com/developers/topics/oauth2)
for the distinction.

## Allow people to use it

Copy [`config/access.example.toml`](../config/access.example.toml) to
`config/access.toml`. Set the
Discord account that owns this installation and, if needed, additional admins:

```toml
owner_id = "123456789012345678"
admin_ids = ["234567890123456789"]
```

The owner and admins can open **Access** in the dashboard. The page lists members
from every Discord server connected to the bot and grants normal listener access
by name. A direct Discord user ID also works when the person is not in that
directory. Normal listener roles and their grant attribution live on the
account in PostgreSQL, so they survive restarts with the rest of the application
data.

The owner may grant or revoke access for anyone. An admin may grant access and
revoke only grants made by that same admin. Owner and admin roles remain in
`config/access.toml` so a database mistake cannot lock every operator out.
Changing those roles requires a backend restart. Grant and revoke actions are
recorded in the durable access history, while a revoked person's active
dashboard sessions end immediately.

Discord user IDs are still useful for direct entry and the operator file. In the
desktop Discord app, open **User Settings** (the gear at the bottom left), then
**Advanced**, and enable **Developer Mode**. Right-click a user in a server,
group chat or DM and choose **Copy User ID**. On mobile, enable Developer Mode
under **Settings > Advanced**, open a user's profile, then use the three-dot
menu to copy their ID. Discord documents both paths in its
[User ID guide](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID).

Keep `config/access.toml` out of Git. If it is missing or invalid, access is
blocked.

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
