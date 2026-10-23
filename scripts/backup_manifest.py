"""Build or verify a hash manifest for an offline, separately taken backup."""
import argparse
import json
from pathlib import Path

from core.backup_manifest import verify_manifest, write_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify bytes of an offline Neo4j backup set")
    parser.add_argument("directory", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.create:
        print(write_manifest(args.directory))
    else:
        print(json.dumps(verify_manifest(args.directory), ensure_ascii=False))


if __name__ == "__main__":
    main()
