from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/202609030002_activity_shadow_publish_timeout.sql"
)


def test_shadow_publish_timeout_is_scoped_to_the_idempotent_rpc() -> None:
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "alter function public.publish_onflows_activity_shadow(" in sql
    assert "set statement_timeout to '45s'" in sql
    assert "alter role" not in sql
    assert "alter database" not in sql
