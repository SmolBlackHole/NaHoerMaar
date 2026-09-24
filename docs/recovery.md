# Back up and restore NaHörMaar

Parent: [Documentation index](README.md)

NaHörMaar keeps the player, queue, Radio, history, accounts and browser sessions
in one PostgreSQL database. [Database](engine/database.md) owns its schema and
transaction boundaries. This page covers operational recovery for the Docker
Compose deployment.

A backup may run while the bot is playing. A restore replaces the active
database, so the backend must be stopped first. The PostgreSQL container stays
running throughout the restore.

## Table of contents

- [Back up and restore NaHörMaar](#back-up-and-restore-nahörmaar)
  - [Table of contents](#table-of-contents)
  - [Create a backup](#create-a-backup)
  - [Verify a backup](#verify-a-backup)
  - [Schedule daily backups](#schedule-daily-backups)
  - [Restore the active database](#restore-the-active-database)
  - [What the maintenance command guarantees](#what-the-maintenance-command-guarantees)

## Create a backup

Run the one-shot maintenance service from the repository root:

```powershell
docker compose --profile maintenance run --rm backup
```

The service creates a PostgreSQL custom-format dump under `data/backups/`,
restores it into a temporary database and checks the Alembic revision and shared
listening session. Only then does it publish the timestamped
`engine-YYYYmmddTHHMMSSZ.dump` file.

The default retention is 14 successful dumps. Set `BACKUP_KEEP` in `.env` to
choose another positive number. Cleanup touches only files matching
`engine-*.dump`; unrelated files remain alone. A failed backup keeps every
previous successful dump.

`data/backups/` is ignored by Git. Copy at least one verified dump to storage
outside the Docker host. The dump contains account data and browser-session
hashes, so protect it like `.env` and `config/access.toml`.

## Verify a backup

Pass the filename, without a directory, to the same maintenance service:

```powershell
docker compose --profile maintenance run --rm backup verify engine-20260923T103000Z.dump
```

Verification checks that `pg_restore` can read the archive, restores it into a
temporary database, and requires the current Alembic revision and exactly one
listening session. The temporary database is removed whether the check succeeds
or fails. A zero exit code means the dump passed those checks.

Run this command after moving a dump to another host. A file existing in the
backup directory is not evidence that it can be restored.

## Schedule daily backups

NaHörMaar does not contain a scheduler. Let the host run the same Compose
command once a day and monitor its exit code.

On Windows, register a task from an elevated PowerShell window opened in the
repository root:

```powershell
$root = (Get-Location).Path
$docker = (Get-Command docker).Source
$action = New-ScheduledTaskAction -Execute $docker -Argument "compose --profile maintenance run --rm backup" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-ScheduledTask -TaskName "NaHörMaar database backup" -Action $action -Trigger $trigger -Description "Create and verify the daily NaHörMaar PostgreSQL backup"
```

A cron entry for a checkout at `/srv/nahormaar` can run the same command:

```cron
0 3 * * * cd /srv/nahormaar && /usr/bin/docker compose --profile maintenance run --rm backup >> data/backups/backup.log 2>&1
```

For systemd, use a one-shot service and timer:

```ini
# /etc/systemd/system/nahormaar-backup.service
[Unit]
Description=Back up the NaHörMaar database

[Service]
Type=oneshot
WorkingDirectory=/srv/nahormaar
ExecStart=/usr/bin/docker compose --profile maintenance run --rm backup
```

```ini
# /etc/systemd/system/nahormaar-backup.timer
[Unit]
Description=Run the NaHörMaar database backup daily

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
Unit=nahormaar-backup.service

[Install]
WantedBy=timers.target
```

Enable the timer with:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nahormaar-backup.timer
```

Check the task or timer after its first run. Scheduling a command does not prove
that Docker could reach the database or write the dump.

## Restore the active database

Choose a verified dump and note the configured `POSTGRES_DB` value. The default
database name is `nahormaar`.

Stop only the backend, then restore with an explicit confirmation matching the
database name:

```powershell
docker compose --profile maintenance run --rm backup verify engine-20260923T103000Z.dump
docker compose stop backend
docker compose --profile maintenance run --rm -e CONFIRM_RESTORE_DATABASE=nahormaar backup restore engine-20260923T103000Z.dump
docker compose start backend
docker compose logs --tail 100 backend
```

The restore command first loads and validates the dump in a separate database.
It then refuses replacement while another client still uses the active
database. After validation, it renames the old database aside, promotes the
restored database, and removes the old copy only after the promotion succeeds.

If `POSTGRES_DB` is not `nahormaar`, use its exact value for
`CONFIRM_RESTORE_DATABASE`. The confirmation prevents an accidental restore from
a copied command. Keep the backend stopped until the restore command finishes.

After startup, check the backend health, Discord channel, queue order, current
track and position, history, Radio state and sign-in. Keep the selected dump
until that acceptance is complete.

## What the maintenance command guarantees

The maintenance service uses PostgreSQL 17 tools against the database service on
the private Compose network. It does not start Discord or playback. Backup and
verification are read-only for the active database. Restore is deliberately a
separate command and requires both a stopped backend and an explicit database
name confirmation.

The database itself lives in the `nahormaar-postgres` named volume. Managed dump
files live in the host directory `data/backups/`. `docker compose down` keeps
the database volume. `docker compose down --volumes` deletes it, so use that
option only when the database is intentionally disposable.
