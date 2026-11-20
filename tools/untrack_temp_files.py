"""Stop git from tracking the database's temporary files (v13.9, 20 Nov 2026).

Run from the project folder:  python tools/untrack_temp_files.py

What it does, automatically:
1. Adds the patterns below to .gitignore if they are missing (keeps everything already there and
   the file's encoding; writes plain UTF-8 text, never UTF-16).
2. Removes matching files from git's tracking (`git rm --cached`). The files stay on your disk;
   they only stop being uploaded to GitHub.
Safe to run more than once: it only changes what is still missing.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

PATTERNS = ["*.db-shm", "*.db-wal", "backup_before_*/"]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    ignore = root / ".gitignore"
    raw = ignore.read_bytes() if ignore.exists() else b""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    lines = [line.strip() for line in text.splitlines()]
    missing = [p for p in PATTERNS if p not in lines]
    if missing:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n# Added by tools/untrack_temp_files.py\n" + "\n".join(missing) + "\n"
        ignore.write_text(text, encoding="utf-8", newline="\n")
        print("Added to .gitignore:", ", ".join(missing))
    else:
        print(".gitignore already has:", ", ".join(PATTERNS))

    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\n")
    def matches(path: str) -> bool:
        for pattern in PATTERNS:
            if pattern.endswith("/"):          # a folder pattern: match the top folder's name
                if "/" in path and fnmatch.fnmatch(path.split("/")[0], pattern.rstrip("/")):
                    return True
            elif fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern):
                return True
        return False

    to_untrack = [f for f in tracked if f and matches(f)]
    if to_untrack:
        subprocess.run(["git", "rm", "-r", "-q", "--cached", "--", *to_untrack], cwd=root, check=True)
        print("Stopped tracking (files kept on disk):")
        for f in to_untrack:
            print("  ", f)
    else:
        print("No tracked temporary files found.")
    print("Now run: git add -A, git commit, git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
