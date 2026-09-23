#!/bin/sh

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

set -eu

command="${1:-backup}"
verification_database=""
restore_database=""
temporary=""

cleanup() {
    if [ -n "$temporary" ]; then
        rm -f "$temporary"
    fi
    if [ -n "$verification_database" ]; then
        dropdb --if-exists "$verification_database" >/dev/null 2>&1 || true
    fi
    if [ -n "$restore_database" ]; then
        dropdb --if-exists "$restore_database" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

managed_dump() {
    case "${1:-}" in
        engine-*.dump)
            dump="/backups/$1"
            ;;
        *)
            echo "Use a managed backup filename such as engine-20260923T103000Z.dump." >&2
            exit 2
            ;;
    esac
    if [ ! -f "$dump" ]; then
        echo "Backup not found: $dump" >&2
        exit 2
    fi
}

check_database() {
    checked_database="$1"
    revision="$(psql --dbname="$checked_database" --tuples-only --no-align --command='SELECT version_num FROM alembic_version')"
    sessions="$(psql --dbname="$checked_database" --tuples-only --no-align --command='SELECT COUNT(*) FROM listening_sessions')"
    if [ "$revision" != "$EXPECTED_SCHEMA_REVISION" ]; then
        echo "Expected schema $EXPECTED_SCHEMA_REVISION, found ${revision:-none}." >&2
        return 1
    fi
    if [ "$sessions" != "1" ]; then
        echo "Expected one listening session, found ${sessions:-none}." >&2
        return 1
    fi
}

restore_into() {
    target_database="$1"
    source_dump="$2"
    dropdb --if-exists "$target_database" >/dev/null 2>&1 || true
    createdb "$target_database"
    pg_restore \
        --exit-on-error \
        --no-owner \
        --no-privileges \
        --dbname="$target_database" \
        "$source_dump"
    check_database "$target_database"
}

verify_dump() {
    source_dump="$1"
    pg_restore --list "$source_dump" >/dev/null
    verification_database="nh_verify_$$"
    restore_into "$verification_database" "$source_dump"
    dropdb "$verification_database"
    verification_database=""
}

mkdir -p /backups

case "$command" in
    backup)
        keep="${BACKUP_KEEP:-14}"
        case "$keep" in
            ''|*[!0-9]*|0)
                echo "BACKUP_KEEP must be a positive integer." >&2
                exit 2
                ;;
        esac
        timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
        destination="/backups/engine-${timestamp}.dump"
        temporary="${destination}.tmp"
        pg_dump \
            --format=custom \
            --no-owner \
            --no-privileges \
            --file="$temporary"
        verify_dump "$temporary"
        mv "$temporary" "$destination"
        temporary=""
        ls -1t /backups/engine-*.dump | sed -n "$((keep + 1)),\$p" | while IFS= read -r stale; do
            rm -f "$stale"
        done
        echo "Backup created and verified: $destination"
        ;;
    verify)
        managed_dump "${2:-}"
        verify_dump "$dump"
        echo "Backup verified: $dump"
        ;;
    restore)
        managed_dump "${2:-}"
        case "$PGDATABASE" in
            ''|[0-9]*|*[!A-Za-z0-9_]*)
                echo "PGDATABASE is not a supported PostgreSQL identifier." >&2
                exit 2
                ;;
        esac
        if [ "${CONFIRM_RESTORE_DATABASE:-}" != "$PGDATABASE" ]; then
            echo "Set CONFIRM_RESTORE_DATABASE=$PGDATABASE to replace the active database." >&2
            exit 2
        fi
        restore_database="nh_restore_$$"
        restore_into "$restore_database" "$dump"
        connections="$(psql --dbname=postgres --tuples-only --no-align --command="SELECT COUNT(*) FROM pg_stat_activity WHERE datname = '$PGDATABASE' AND pid <> pg_backend_pid()")"
        if [ "$connections" != "0" ]; then
            echo "Refusing restore while $connections client connection(s) use $PGDATABASE." >&2
            exit 1
        fi
        old_database="nh_before_restore_$$"
        psql --dbname=postgres --set=ON_ERROR_STOP=1 --command="ALTER DATABASE \"$PGDATABASE\" RENAME TO \"$old_database\"" >/dev/null
        if ! psql --dbname=postgres --set=ON_ERROR_STOP=1 --command="ALTER DATABASE \"$restore_database\" RENAME TO \"$PGDATABASE\"" >/dev/null; then
            psql --dbname=postgres --set=ON_ERROR_STOP=1 --command="ALTER DATABASE \"$old_database\" RENAME TO \"$PGDATABASE\"" >/dev/null
            exit 1
        fi
        restore_database=""
        dropdb "$old_database"
        echo "Database restored from: $dump"
        ;;
    *)
        echo "Usage: postgres-backup.sh {backup|verify <filename>|restore <filename>}" >&2
        exit 2
        ;;
esac

trap - EXIT INT TERM
