#!/usr/bin/env python3
"""Resolve an audit request to a public repository and run the audit.

Read-only: this script only reads public GitHub data through repo_audit.py and
writes the report into the workspace. It never writes to the audited repository.

Inputs (environment variables):
  DISPATCH_REPO  owner/name passed to a workflow_dispatch run (optional)
  ISSUE_TITLE    title of the requesting issue (optional)
  ISSUE_BODY     body of the requesting issue (optional)

Outputs (written to $GITHUB_OUTPUT when present):
  repo      resolved owner/name
  ok        "true" or "false"
  files     space-separated report files that were produced
"""

import os
import re
import subprocess
import sys

REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?/[A-Za-z0-9._-]{1,100}$")
URL_RE = re.compile(r"github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")


def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("%s=%s\n" % (name, value))


def candidate_from(text):
    """Extract an owner/name candidate from a URL or a bare slug."""
    if not text:
        return None
    for owner, name in URL_RE.findall(text):
        name = name.strip("().,`'\"<>*")
        if name.endswith(".git"):
            name = name[:-4]
        name = name.rstrip("./")
        if REPO_RE.match("%s/%s" % (owner, name)):
            return "%s/%s" % (owner, name)
    for raw in re.split(r"[\s,;]+", text):
        token = raw.strip().strip("().,`'\"<>*")
        if "/" not in token:
            continue
        token = token.rstrip("/").removesuffix(".git")
        if REPO_RE.match(token):
            return token
    return None


def resolve_repo():
    sources = [
        ("dispatch", os.environ.get("DISPATCH_REPO", "")),
        ("issue body", os.environ.get("ISSUE_BODY", "")),
        ("issue title", os.environ.get("ISSUE_TITLE", "")),
    ]
    for _, text in sources:
        found = candidate_from(text)
        if found:
            return found
    return None


def main():
    repo = resolve_repo()
    if not repo:
        print("Could not find a public owner/name repository in the request.",
              file=sys.stderr)
        set_output("ok", "false")
        return 1

    print("Auditing %s" % repo)
    result = subprocess.run(
        [sys.executable, "repo_audit.py", repo,
         "--teaser", "--out", "report.md"],
        text=True, capture_output=True,
    )
    if result.returncode != 0 or not os.path.exists("report.md"):
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        print("Audit failed: %s" % (tail[-1] if tail else "unknown error"),
              file=sys.stderr)
        set_output("ok", "false")
        set_output("repo", repo)
        return 1

    files = [f for f in ("report.md",) if os.path.exists(f)]
    print("Wrote %s" % ", ".join(files))
    set_output("ok", "true")
    set_output("repo", repo)
    set_output("files", " ".join(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
