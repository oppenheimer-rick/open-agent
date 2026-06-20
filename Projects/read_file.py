#!/usr/bin/env python3
"""read_file.py — Read a file's contents to stdout."""

import argparse
import sys


def read_file(path: str) -> str:
    """Read and return the contents of *path* as a string."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a file and print its contents.")
    parser.add_argument("file", help="Path to the file to read")
    args = parser.parse_args()

    try:
        content = read_file(args.file)
        print(content, end="")
    except FileNotFoundError:
        print(f"Error: '{args.file}' not found.", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: permission denied for '{args.file}'.", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"Error reading '{args.file}': {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
