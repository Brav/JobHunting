"""One live LinkedIn search, to check the scraper still works.

Usage: python -m jobhunt.smoke "<keywords>" "<location>" [radius_km]
"""

import argparse
import sys

from jobhunt.sources.base import JobSource, SourceError


def main(argv: list[str] | None = None, source: JobSource | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobhunt.smoke", description=__doc__.splitlines()[0])
    parser.add_argument("keywords")
    parser.add_argument("location")
    parser.add_argument("radius_km", nargs="?", type=int, default=25)
    args = parser.parse_args(argv)
    if source is None:
        from jobhunt.sources.linkedin import LinkedInSource
        source = LinkedInSource()
    try:
        postings = source.search(keywords=args.keywords, location=args.location,
                                 radius_km=args.radius_km, window_hours=168, limit=25)
    except SourceError as e:
        print(f"search failed: {e}", file=sys.stderr)
        return 1
    for job in postings:
        print(f"{job.external_id} | {job.posted_at or '-'} | {job.title} | {job.company} | {job.location}")
    print(f"{len(postings)} jobs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
