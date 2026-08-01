#!/usr/bin/env python3
"""Generate a Keep a Changelog style CHANGELOG.md from conventional commits.

Python 3 standard library only. No config file, no node_modules.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date

SECTIONS = [
    ("Added", ("feat",)),
    ("Fixed", ("fix",)),
    ("Changed", ("refactor", "style", "build", "ci", "chore")),
    ("Removed", ("remove", "revert")),
    ("Performance", ("perf",)),
    ("Docs", ("docs", "test")),
    ("Other", ()),
]

TYPE_TO_SECTION = {}
for _name, _types in SECTIONS:
    for _t in _types:
        TYPE_TO_SECTION[_t] = _name

CONVENTIONAL = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$"
)
ISSUE = re.compile(r"#(\d+)")


class GitError(RuntimeError):
    pass


def git(*args):
    proc = subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout).strip())
    return proc.stdout


def tags_by_date():
    out = git("tag", "--sort=-creatordate")
    return [t.strip() for t in out.splitlines() if t.strip()]


def commit_range(frm, to):
    if frm and to:
        return "%s..%s" % (frm, to)
    if frm:
        return "%s..HEAD" % frm
    if to:
        return to
    return None


def read_commits(rng):
    sep = "\x1e"
    fmt = "%H%x1f%h%x1f%s%x1f%b" + sep
    args = ["log", "--no-merges", "--format=" + fmt]
    if rng:
        args.append(rng)
    raw = git(*args)
    commits = []
    for chunk in raw.split(sep):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f")
        while len(parts) < 4:
            parts.append("")
        commits.append(
            {
                "sha": parts[0],
                "short": parts[1],
                "subject": parts[2],
                "body": parts[3],
            }
        )
    return commits


def classify(commit):
    m = CONVENTIONAL.match(commit["subject"])
    breaking = "BREAKING CHANGE" in commit["body"] or "BREAKING-CHANGE" in commit["body"]
    if not m:
        return {
            "section": "Other",
            "scope": None,
            "subject": commit["subject"],
            "breaking": breaking,
            "conventional": False,
        }
    ctype = m.group("type").lower()
    if m.group("bang"):
        breaking = True
    return {
        "section": TYPE_TO_SECTION.get(ctype, "Other"),
        "scope": m.group("scope"),
        "subject": m.group("subject"),
        "breaking": breaking,
        "conventional": ctype in TYPE_TO_SECTION,
    }


def link_issues(text, repo_url):
    if not repo_url:
        return text
    base = repo_url.rstrip("/")
    return ISSUE.sub(lambda m: "[#%s](%s/issues/%s)" % (m.group(1), base, m.group(1)), text)


def commit_link(commit, repo_url):
    if not repo_url:
        return "`%s`" % commit["short"]
    return "[`%s`](%s/commit/%s)" % (commit["short"], repo_url.rstrip("/"), commit["sha"])


def render_entry(commit, info, repo_url):
    subject = link_issues(info["subject"], repo_url)
    scope = "**%s:** " % info["scope"] if info["scope"] else ""
    return "- %s%s (%s)" % (scope, subject, commit_link(commit, repo_url))


def render_release(title, commits, repo_url, when=None):
    when = when or date.today().isoformat()
    lines = ["## [%s] - %s" % (title, when), ""]

    buckets = {name: [] for name, _ in SECTIONS}
    breaking = []
    skipped = 0

    for commit in commits:
        info = classify(commit)
        entry = render_entry(commit, info, repo_url)
        if info["breaking"]:
            breaking.append(entry)
        else:
            buckets[info["section"]].append(entry)
        if not info["conventional"]:
            skipped += 1

    if breaking:
        lines.append("### :warning: Breaking changes")
        lines.append("")
        lines.extend(breaking)
        lines.append("")

    for name, _ in SECTIONS:
        if not buckets[name]:
            continue
        lines.append("### %s" % name)
        lines.append("")
        lines.extend(buckets[name])
        lines.append("")

    if skipped:
        lines.append(
            "_%d commit%s did not follow conventional commits. They are listed under Other, not dropped._"
            % (skipped, "s" if skipped != 1 else "")
        )
        lines.append("")

    if not commits:
        lines.append("_No commits in this range._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


HEADER = """# Changelog

All notable changes to this project are documented in this file.
The format is based on Keep a Changelog and this project follows semantic versioning.
"""


def write_in_place(path, release_md):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    else:
        existing = HEADER

    idx = existing.find("\n## ")
    if idx == -1:
        head, tail = existing.rstrip() + "\n", ""
    else:
        head, tail = existing[:idx].rstrip() + "\n", existing[idx + 1 :]

    new = head + "\n" + release_md + ("\n" + tail if tail else "")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="changelog_from_git",
        description="Generate CHANGELOG.md from conventional commits.",
    )
    parser.add_argument("--from", dest="frm", help="start tag (exclusive)")
    parser.add_argument("--to", dest="to", help="end tag (inclusive)")
    parser.add_argument("--unreleased", action="store_true", help="commits since the latest tag")
    parser.add_argument("--repo-url", help="e.g. https://github.com/you/repo, enables links")
    parser.add_argument("--title", help="release heading, defaults to the --to tag or Unreleased")
    parser.add_argument("--in-place", action="store_true", help="prepend to CHANGELOG.md")
    parser.add_argument("--file", default="CHANGELOG.md", help="target file for --in-place")
    args = parser.parse_args(argv)

    try:
        git("rev-parse", "--git-dir")
    except GitError:
        print("not a git repository", file=sys.stderr)
        return 2

    tags = tags_by_date()
    frm, to = args.frm, args.to

    if args.unreleased or (not frm and not to):
        if tags and not frm:
            frm = tags[0]
        to = to or None

    title = args.title
    if not title:
        title = to if to else "Unreleased"

    try:
        commits = read_commits(commit_range(frm, to))
    except GitError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    release = render_release(title, commits, args.repo_url)

    if args.in_place:
        path = write_in_place(args.file, release)
        print("wrote %d entries to %s" % (len(commits), path), file=sys.stderr)
    else:
        sys.stdout.write(release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
