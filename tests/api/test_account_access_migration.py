from pathlib import Path


MIGRATION = Path("supabase/migrations/202609020001_accounts_sharing_organizations_v1.sql")


def test_account_access_migration_has_expected_foundation_and_rls():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    expected_tables = (
        "onflows_profiles",
        "onflows_user_athletes",
        "onflows_connection_invites",
        "onflows_sharing_grants",
        "onflows_organizations",
        "onflows_organization_memberships",
        "onflows_coach_athlete_assignments",
        "onflows_organization_settings",
        "onflows_plan_authorities",
        "onflows_access_audit_log",
    )
    for table in expected_tables:
        assert f"create table public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql


def test_plan_editing_is_explicit_and_single_authority_is_enforced():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "edit_plan boolean not null default false" in sql
    assert "check (not edit_plan or view_plan)" in sql
    assert "athlete_user_id uuid primary key" in sql


def test_authorization_does_not_trust_user_metadata():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "user_metadata" not in sql
    assert "auth.uid()" in sql
    assert "revoke all on schema private from public, anon, authenticated" in sql
