#!/usr/bin/env python3
"""Keep the free-audit reply idempotent and compose it.

GitHub fires the `issues` webhook once per `opened`, `labeled` and `edited`
event, and the request template applies its label at creation, so a single
request can trigger the audit workflow several times. Without a guard the same
report is posted once per run. This module holds the pure logic that decides
whether a reply is still needed so it can be unit-tested without Actions.

CLI: `python3 audit_reply.py guard` reads a JSON array of comment bodies on
stdin and writes `needs_report=` / `needs_failure=` lines for $GITHUB_OUTPUT.
"""

import json
import sys

# Sentinels that must also appear in the reply text below.
REPORT_MARKER = "free read-only health scan"
FAILURE_MARKER = "include a public repository URL"

# The text is generated from here so the workflow and the guard cannot drift.
FAILURE_TEXT = (
    "I could not find a public `owner/name` repository in this request. "
    "Please include a public repository URL such as `https://github.com/owner/repo` "
    "and I will re-run the scan."
)


def failure_reply():
    """Compose the reply for a request with no resolvable repository."""
    return FAILURE_TEXT


def already_replied(comments, marker=REPORT_MARKER):
    """True when any existing comment body contains the marker.

    Accepts either a list of body strings (what `--jq '.comments[].body'`
    emits) or a list of comment objects, so the caller cannot get it wrong.
    """
    for item in comments or []:
        body = item.get("body") if isinstance(item, dict) else item
        if marker in (body or ""):
            return True
    return False


def guard(comments):
    """Return the $GITHUB_OUTPUT lines for a list of existing comment bodies."""
    return "needs_report=%s\nneeds_failure=%s\n" % (
        str(not already_replied(comments, REPORT_MARKER)).lower(),
        str(not already_replied(comments, FAILURE_MARKER)).lower(),
    )


def build_reply(repo, report_text,
                offer_url="https://agentbarto90-eng.github.io/repo-health-audit/"):
    """Compose the free-scan reply posted back onto the request issue."""
    return "\n".join([
        "Thanks — here is the %s of `%s`." % (REPORT_MARKER, repo),
        "",
        report_text.rstrip(),
        "",
        "Read-only: no change was made to your repository.",
        "",
        "The free scan shows the score, the dimension breakdown and the top "
        "finding titles. The full audit writes up every finding, turns each one "
        "into a ready-to-file issue draft with labels, and adds a fix pack of "
        "files you can review and commit. Reply here or use the offer page "
        "(%s) if you want the full audit." % offer_url,
    ])


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] != ["guard"]:
        print("usage: audit_reply.py guard  (comment bodies as a JSON array on stdin)",
              file=sys.stderr)
        return 2
    try:
        comments = json.load(sys.stdin)
    except ValueError:
        comments = []
    if not isinstance(comments, list):
        comments = []
    sys.stdout.write(guard(comments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
