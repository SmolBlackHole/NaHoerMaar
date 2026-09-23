# Back up and restore NaHörMaar

Parent: [Documentation index](README.md) | [Hosting considerations](hosting.md)

NaHörMaar keeps the player, queue, Radio, history, accounts and browser sessions
in one SQLite database. `DATABASE_PATH` selects that file and defaults to
`data/engine.sqlite3`.

The recovery command creates a consistent backup while the backend is running.
Restoring is different: stop the backend before replacing its database. The
dashboard can stay built or served, but it cannot control the player while the
backend is offline.

## Create and check a backup

Run this from the repository root:

```powershell
.venv\Scripts\python.exe -m nahormaar_backend.recovery backup
```

Linux and macOS use `.venv/bin/python` instead. The command reads
`DATABASE_PATH` from the environment or `.env`, writes a timestamped file to a
`backups` directory beside the database, verifies it and keeps the newest 14
managed backups. A failed backup does not remove an older one. Files that do not
match NaHörMaar's backup naming scheme are left alone.

Use `--directory` or `--keep` when the defaults do not fit the host:

```powershell
.venv\Scripts\python.exe -m nahormaar_backend.recovery backup --directory E:\Backups\NaHörMaar --keep 30
```

Check any backup independently:

```powershell
.venv\Scripts\python.exe -m nahormaar_backend.recovery verify data\backups\engine-20260923T103000000000Z.sqlite3
```

Verification checks SQLite integrity, foreign keys, the current engine schema
and the single listening session. A zero exit code means the file passed those
checks. It does not prove that the storage device will still be available after
a host failure. Keep at least one copy outside the machine that runs the bot.

The backup contains account data and active browser-session hashes. Store it
with the same care as `.env` and `access.toml`.

## Schedule a daily backup

The bot does not run its own scheduler. The host starts the same `backup`
command once a day.

On Windows, create a task from an elevated PowerShell session while the current
directory is the repository root:

```powershell
$root = (Get-Location).Path
$python = (Resolve-Path .venv\Scripts\python.exe).Path
$action = New-ScheduledTaskAction -Execute $python -Argument "-m nahormaar_backend.recovery backup" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-ScheduledTask -TaskName "NaHörMaar database backup" -Action $action -Trigger $trigger -Description "Create and verify the daily NaHörMaar SQLite backup"
```

The task runs as the account that registers it. Make sure that account can read
the database and write to the backup directory.

A cron entry for a checkout at `/srv/nahormaar` looks like this:

```cron
0 3 * * * cd /srv/nahormaar && .venv/bin/python -m nahormaar_backend.recovery backup >> data/backups/backup.log 2>&1
```

For systemd, use a one-shot service and timer:

```ini
# /etc/systemd/system/nahormaar-backup.service
[Unit]
Description=Back up the NaHörMaar database

[Service]
Type=oneshot
WorkingDirectory=/srv/nahormaar
ExecStart=/srv/nahormaar/.venv/bin/python -m nahormaar_backend.recovery backup
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

Enable it with:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nahormaar-backup.timer
```

Check the task or timer after its first run. The command prints the full path of
the completed backup and returns a nonzero exit code on failure.

## Restore into a fresh database

Restoring to another path is safe to rehearse without touching the active file:

```powershell
.venv\Scripts\python.exe -m nahormaar_backend.recovery restore data\backups\engine-20260923T103000000000Z.sqlite3 --database data\restored.sqlite3
```

Point `DATABASE_PATH` at `data/restored.sqlite3` only after the command succeeds.
Start the backend and check `engine.runtime.ready`, sign-in, the queue, the
current position, history and Radio state. This is also the preferred way to
test a backup on another machine.

## Replace the active database

1. Verify the chosen backup.
2. If the current database is readable, create one final backup.
3. Stop the backend and wait for the process to exit.
4. Restore with explicit replacement:

   ```powershell
   .venv\Scripts\python.exe -m nahormaar_backend.recovery restore data\backups\engine-20260923T103000000000Z.sqlite3 --replace
   ```

5. Start the backend and inspect its startup logs.
6. Open the dashboard and check the account, queue, current position, history
   and Radio state before resuming playback.

`--replace` is deliberately required. The command validates a temporary copy
before swapping it into place, so a damaged or unrelated backup leaves the
destination untouched. It cannot reliably determine on every operating system
whether another process still owns the database. Replacing a database while the
backend is running is unsupported.
