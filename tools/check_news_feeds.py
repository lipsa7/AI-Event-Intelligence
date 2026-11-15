"""
Check every feed in config/news_feeds.txt (active lines and '#candidate' lines)
from this computer, in one run, and report which work.

    python -m tools.check_news_feeds                    # report only
    python -m tools.check_news_feeds --enable-working   # also uncomment working candidates
                                                        # and comment out failing active feeds

A feed "works" when it parses and its newest item is at most --max-age-days old.
The original file is backed up as news_feeds.txt.bak before any change.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from services.free_news_providers import RSSFeedProvider, _parse_date

FEED_FILE = Path("config") / "news_feeds.txt"


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def feed_urls(lines: list[str]) -> list[tuple[int, str, bool]]:
    rows = []
    for number, line in enumerate(lines):
        text = line.strip()
        if text.startswith("#candidate"):
            # The URL is the first token; anything after it (for example
            # "# failed 2026-10-04: ...") is a note, not part of the URL.
            parts = text[len("#candidate"):].split()
            if parts and parts[0].startswith(("http://", "https://")):
                rows.append((number, parts[0], False))
        elif text and not text.startswith("#"):
            parts = text.split("#", 1)[0].split()
            if parts:
                rows.append((number, parts[0], True))
    return rows


def check(url: str, max_age_days: float) -> tuple[bool, str]:
    try:
        items = RSSFeedProvider._fetch_with_project_service(url, 15.0)
    except Exception as error:
        return False, f"{type(error).__name__}: {str(error)[:120]}"
    if not items:
        return False, "no items"
    dates = [d for d in (_parse_date(i.get("published_at") or i.get("published")) for i in items if isinstance(i, dict)) if d]
    if not dates:
        return True, f"{len(items)} items (no dates)"
    newest = max(dates)
    age = (datetime.now(timezone.utc) - newest).total_seconds() / 86400
    if age > max_age_days:
        return False, f"{len(items)} items, newest {age:.0f} days old"
    return True, f"{len(items)} items, newest {newest:%Y-%m-%d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=FEED_FILE)
    parser.add_argument("--max-age-days", type=float, default=14.0)
    parser.add_argument("--enable-working", action="store_true")
    args = parser.parse_args()
    lines = read_lines(args.file)
    changed = False
    working = 0
    for number, url, active in feed_urls(lines):
        ok, detail = check(url, args.max_age_days)
        working += int(ok)
        state = "active" if active else "candidate"
        print(f"{'OK  ' if ok else 'FAIL'} [{state:9}] {url}  ({detail})")
        if args.enable_working:
            if ok and not active:
                lines[number] = url
                changed = True
            elif not ok and active:
                lines[number] = f"#candidate {url}  # failed {datetime.now():%Y-%m-%d}: {detail}"
                changed = True
    if changed:
        shutil.copy2(args.file, args.file.with_suffix(".txt.bak"))
        args.file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Updated {args.file} (backup: {args.file.with_suffix('.txt.bak')})")
    print(f"{working} feed(s) working.")
    return 0 if working else 1


if __name__ == "__main__":
    raise SystemExit(main())
