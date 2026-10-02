"""Bounded history maintenance. Preview by default; no provider requests."""
import argparse
import json

from .history_archive import restore, run, verify_backup
from .oauth_store import SupabasePilotRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-rows", type=int, default=2)
    parser.add_argument("--backup-kind", choices=("shadow", "input"))
    parser.add_argument("--restore-kind", choices=("shadow", "input"))
    parser.add_argument("--key", default="")
    args = parser.parse_args()
    if (args.backup_kind and args.restore_kind) or (args.apply and (args.backup_kind or args.restore_kind)):
        parser.error("Choose a single maintenance operation")
    if bool(args.key) != bool(args.backup_kind or args.restore_kind):
        parser.error("Explicit backup/restore requires kind and key")
    repository = SupabasePilotRepository.from_environment()
    try:
        if args.restore_kind:
            result = restore(repository, args.restore_kind, args.key)
        elif args.backup_kind:
            result = verify_backup(repository, args.backup_kind, args.key)
        else:
            result = run(repository, apply=args.apply, max_rows=args.max_rows)
        print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as exc:
        print(json.dumps({"status": "stopped", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    finally:
        repository._client.close()


if __name__ == "__main__":
    main()
