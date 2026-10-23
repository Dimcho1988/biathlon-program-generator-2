"""Preparation must refuse incomplete or unequal evidence; no database calls."""
import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("migration_audit", ROOT / "scripts/audit_migration_alignment.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def catalog(*rows, major=17):
    return {"format": "onflows-catalog-v1", "postgres_major": major, "objects": [
        {"kind": kind, "identity": identity, "definition_sha256": digest}
        for kind, identity, digest in rows]}


class MigrationEvidenceTests(unittest.TestCase):
    def test_all_reviewed_source_bytes_and_order_are_preserved(self):
        self.assertEqual(audit.verify_files()["rename_count"], 18)

    def test_missing_extra_changed_and_major_differences_are_not_normalized(self):
        expected = catalog(("constraint", "public.onflows_sync_jobs.check", "a" * 64),
                           ("relation", "public.onflows_sync_jobs", "b" * 64))
        self.assertEqual(audit.compare_catalogs(expected, expected), [])
        observed = catalog(("relation", "public.onflows_sync_jobs", "c" * 64),
                           ("function", "public.unexpected()", "d" * 64), major=15)
        differences = audit.compare_catalogs(expected, observed)
        self.assertEqual(len(differences), 4)
        self.assertTrue(any("major version" in difference for difference in differences))
        self.assertTrue(any("Missing object: constraint:" in difference for difference in differences))
        self.assertTrue(any("Unexpected object: function:" in difference for difference in differences))
        self.assertTrue(any("Changed definition/ownership/security:" in difference for difference in differences))

    def test_duplicate_catalog_identity_and_bad_hash_fail(self):
        row = ("relation", "public.onflows_sync_jobs", "a" * 64)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            audit.catalog_rows(catalog(row, row))
        with self.assertRaisesRegex(ValueError, "digest"):
            audit.catalog_rows(catalog((row[0], row[1], "unverified")))

    def test_matching_but_incomplete_catalog_cannot_prepare_repair(self):
        incomplete = catalog(("relation", "public.onflows_sync_jobs", "a" * 64))
        before = audit.read_json(audit.BEFORE)
        with self.assertRaisesRegex(ValueError, "23 baseline tables"):
            audit.prepare_repair(incomplete, incomplete, before, ci_commit="a" * 40,
                ci_run_url="https://github.com/Dimcho1988/biathlon-program-generator-2/actions/runs/1")

    def test_sql_text_literals_and_dollar_delimiter_are_independent(self):
        self.assertEqual(audit.sql_literal("athlete's schema"), "'athlete''s schema'")
        body = "begin perform '$baseline$'; end;"
        wrapped = audit.do_block(body)
        self.assertIn(body, wrapped)
        tag = wrapped.split()[1]
        self.assertNotIn(tag, body)
        self.assertEqual(wrapped.count(tag), 2)
        with self.assertRaises(ValueError):
            audit.sql_literal("bad\x00literal")

    def test_new_metadata_requires_explicit_file_and_preserves_every_old_row(self):
        before = [{"version": "20261019193059", "name": "old", "statement_count": 1,
                   "joined_bytes": 9, "joined_sha256": "a" * 64}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "supabase/migrations"
            folder.mkdir(parents=True)
            path = folder / "20261019202610_reconcile.sql"
            raw = b"select 1;\n"
            path.write_bytes(raw)
            row = dict(version="20261019202610", name="reconcile", statement_count=1,
                       joined_bytes=len(raw), joined_sha256=hashlib.sha256(raw).hexdigest())
            metadata = {"format": "onflows-migration-metadata-v1", "rows": before + [row]}
            self.assertEqual(audit.reviewed_metadata_rows(metadata, before, [path], root), before + [row])
            with self.assertRaisesRegex(ValueError, "Original 25"):
                audit.reviewed_metadata_rows(metadata, before, [], root)
            changed = {**metadata, "rows": [{**before[0], "joined_sha256": "b" * 64}, row]}
            with self.assertRaisesRegex(ValueError, "Original 25"):
                audit.reviewed_metadata_rows(changed, before, [path], root)
            path.write_bytes(b"select 2;\n")
            with self.assertRaisesRegex(ValueError, "exact SQL hash differs"):
                audit.reviewed_metadata_rows(metadata, before, [path], root)


if __name__ == "__main__":
    unittest.main()
