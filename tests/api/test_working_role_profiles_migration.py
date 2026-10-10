from pathlib import Path


MIGRATION = Path("supabase/migrations/202609020002_working_role_profiles.sql")
FOLLOWUP_MIGRATION = Path("supabase/migrations/202609030001_role_profile_security_followup.sql")


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def followup_migration_sql() -> str:
    return FOLLOWUP_MIGRATION.read_text(encoding="utf-8").lower()


def test_working_roles_are_database_authorized_not_ui_only():
    sql = migration_sql()
    assert "private.onflows_can_access_athlete" in sql
    assert "viewer.role in ('admin', 'head_coach')" in sql
    assert "viewer.role = 'coach'" in sql
    assert "onflows_coach_athlete_assignments" in sql
    assert "athlete.role = 'athlete'" in sql
    assert "create policy user_athletes_select" in sql


def test_role_rpc_entry_points_are_authenticated_and_least_privilege():
    sql = migration_sql()
    for signature in (
        "public.create_onflows_organization(text, text)",
        "public.accept_onflows_organization_invite(uuid)",
    ):
        assert f"revoke all on function {signature} from public, anon" in sql
        assert f"grant execute on function {signature} to authenticated" in sql
    assert "caller_id uuid := (select auth.uid())" in sql
    assert "from auth.users" in sql
    assert "lower(invite_row.invitee_email) <> caller_email" in sql


def test_assignment_membership_roles_are_enforced_by_trigger():
    sql = migration_sql()
    assert "create trigger validate_onflows_assignment_memberships" in sql
    assert "m.role in ('head_coach', 'coach')" in sql
    assert "m.role = 'athlete'" in sql
    assert "private.onflows_org_role(new.organization_id)) not in ('admin', 'head_coach')" in sql
    assert "revoke all on function private.validate_onflows_assignment_memberships() from public, anon, authenticated" in sql


def test_pending_invites_are_scoped_by_verified_jwt_email_and_indexed():
    sql = migration_sql()
    assert "auth.jwt() ->> 'email'" in sql
    assert "lower(invitee_email), expires_at" in sql
    assert "where status = 'pending'" in sql
    assert "membership_role in ('coach', 'athlete')" in sql


def test_direct_role_escalation_and_non_atomic_membership_writes_are_revoked():
    sql = migration_sql()
    assert "drop policy invites_update" in sql
    assert "revoke update on public.onflows_connection_invites from authenticated" in sql
    assert "drop policy organizations_insert" in sql
    assert "revoke insert, update on public.onflows_organizations from authenticated" in sql
    assert "drop policy memberships_update" in sql
    assert "revoke insert, update, delete on public.onflows_organization_memberships from authenticated" in sql


def test_security_followup_handles_clean_databases_without_auto_rls_function():
    sql = followup_migration_sql()
    assert "to_regprocedure('public.rls_auto_enable()') is not null" in sql
    assert "execute 'revoke all on function public.rls_auto_enable() from public, anon, authenticated'" in sql
