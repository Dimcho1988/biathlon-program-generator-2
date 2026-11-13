# Supabase migration workflow

The SQL files in `supabase/migrations/` are the only source of truth for remote
schema changes. Do not change staging or production schemas through the
Dashboard Table Editor or SQL Editor.

## Existing-project baseline

The eleven migrations through `202608270002` existed before remote migration
history was enabled. For each environment, first compare the live schema with
those files and then mark those exact versions as applied with the official CLI.
Production currently uses this baseline:

```bash
supabase link --project-ref <project-ref>
supabase migration repair 202608150001 202608160001 202608160002 202608180001 202608180002 202608190001 202608220001 202608220002 202608230001 202608270001 202608270002 --status applied
supabase migration list
```

The accounts migration `202609020001` was exercised directly on staging while
the workflow was being introduced. After its schema is verified, staging must
also mark that version as applied:

```bash
supabase migration repair 202609020001 --status applied
```

Never run `db push` before the environment-specific baseline is verified.
Baseline staging first. Production is repaired only after a separate review and
approval; do not mark `202609020001` as applied in production until the migration
has actually been deployed there.

## New changes

1. Create a file with `supabase migration new <name>`.
2. Review SQL, RLS, grants, constraints and backward compatibility.
3. Reset/test against a local Supabase database.
4. Open a pull request and pass CI.
5. Apply to staging with `supabase db push --linked --dry-run`, then `supabase db push --linked`.
6. Run database advisors and staging acceptance tests.
7. Apply the same immutable migration to production only after approval.

CI credentials must be short-lived or environment-scoped. Staging and
production use separate GitHub Environments; production requires a reviewer.
Database passwords, service-role keys and access tokens must never be committed.
