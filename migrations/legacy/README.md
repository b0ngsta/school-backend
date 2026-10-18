# Legacy SQL (before Alembic)

These files built and upgraded the database before migrations moved to
Alembic. They are kept for reference and for very old databases only.

* A database created from any of these files is adopted automatically by
  `alembic upgrade head` (revision `0001_baseline_v7` detects it), as long as
  it is at least at v7 (has the `calendar_events` table).
* A database older than v7: apply the missing `migration_v3.sql` …
  `migration_v7.sql` first, then run `alembic upgrade head`.
* `migration_v8.sql` does not need to be run by hand any more; revision
  `0002_auth_audit_v8` applies the same changes and skips whatever is
  already there.

Do not add new files here — create an Alembic revision instead
(`alembic revision -m "describe the change"`).
