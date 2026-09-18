#!/usr/bin/env python3
"""Open-source repo health audit.

Reads a public GitHub repository through the REST API and scores five
dimensions of maintainability: documentation, testing, CI/CD, issue triage and
maintenance. Emits a Markdown report plus ready-to-file issue drafts, so a
maintainer can go from "what is missing" to "issues open" in one pass.

Read-only: it never writes to the audited repository. The only credential it
needs is a GitHub token, read from GITHUB_TOKEN or the gh CLI.

Usage:
    python3 repo_audit.py owner/repo [--out report.md] [--issues issues.md] [--json]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
UA = "repo-health-audit/1.0"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:
        return subprocess.check_output(
            ["gh", "auth", "token"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return None


class Api:
    def __init__(self, tok):
        self.tok = tok
        self.requests = 0

    def get(self, path, params=None, retries=2):
        url = path if path.startswith("http") else API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "User-Agent": UA,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.tok:
            headers["Authorization"] = "Bearer " + self.tok
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, headers=headers)
            self.requests += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8", "replace")
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) and attempt < retries:
                    time.sleep(2 + attempt * 2)
                    continue
                return {"_error": e.code, "_url": url}
            except Exception as e:  # network hiccup
                if attempt < retries:
                    time.sleep(1 + attempt)
                    continue
                return {"_error": str(e), "_url": url}
        return {"_error": "unreachable"}


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect(api, full_name):
    owner, _, name = full_name.partition("/")
    data = {}

    repo = api.get(f"/repos/{full_name}")
    if not isinstance(repo, dict) or repo.get("_error"):
        raise SystemExit("Could not read %s: %s" % (full_name, repo))
    data["repo"] = repo

    data["languages"] = api.get(f"/repos/{full_name}/languages") or {}
    data["community"] = api.get(f"/repos/{full_name}/community/profile") or {}

    root = api.get(f"/repos/{full_name}/contents/")
    data["root"] = root if isinstance(root, list) else []

    gh_dir = api.get(f"/repos/{full_name}/contents/.github")
    data["dotgithub"] = gh_dir if isinstance(gh_dir, list) else []

    wf = api.get(f"/repos/{full_name}/actions/workflows")
    all_workflows = (wf or {}).get("workflows", []) if isinstance(wf, dict) else []
    # GitHub generates hidden workflows (e.g. dynamic/pages/pages-build-deployment).
    # They are not maintainer CI, so keep them out of the authored set.
    data["generated_workflows"] = [w for w in all_workflows if is_generated_workflow(w)]
    data["workflows"] = [w for w in all_workflows if not is_generated_workflow(w)]

    data["workflow_files"] = {}
    for w in data["workflows"][:8]:
        path = w.get("path") or ""
        if not path:
            continue
        f = api.get(f"/repos/{full_name}/contents/{path}")
        content = _decode_content(f)
        if content:
            data["workflow_files"][path] = content

    data["runs"] = []
    if data["workflows"]:
        runs = api.get(
            f"/repos/{full_name}/actions/runs",
            {"per_page": 20, "exclude_pull_requests": "false"},
        )
        if isinstance(runs, dict):
            data["runs"] = runs.get("workflow_runs", []) or []

    # Issue triage: label coverage over recent open issues and PRs.
    open_issues = api.get(
        f"/repos/{full_name}/issues",
        {"state": "open", "per_page": 100, "sort": "created", "direction": "desc"},
    )
    data["open_issues"] = open_issues if isinstance(open_issues, list) else []

    labels = api.get(f"/repos/{full_name}/labels", {"per_page": 100})
    data["labels"] = labels if isinstance(labels, list) else []

    tags = api.get(f"/repos/{full_name}/tags", {"per_page": 10})
    data["tags"] = tags if isinstance(tags, list) else []

    releases = api.get(f"/repos/{full_name}/releases", {"per_page": 5})
    data["releases"] = releases if isinstance(releases, list) else []

    commits = api.get(f"/repos/{full_name}/commits", {"per_page": 50})
    data["commits"] = commits if isinstance(commits, list) else []

    data["branch_protection"] = api.get(
        f"/repos/{full_name}/branches/{repo.get('default_branch', 'main')}/protection"
    )

    # Dependabot / Renovate presence from known filenames.
    root_names = {(_lower(i.get("name", ""))) for i in data["root"]}
    gh_names = {_lower(i.get("name", "")) for i in data["dotgithub"]}
    data["has_dependabot"] = "dependabot.yml" in gh_names or "dependabot.yaml" in gh_names
    data["has_renovate"] = bool(
        {"renovate.json", "renovate.json5", ".renovaterc", ".renovaterc.json"}
        & (root_names | gh_names)
    )
    return data


def _lower(v):
    return (v or "").lower()


def _decode_content(entry):
    """Decode a contents API file object to text, or return None."""
    import base64

    if not isinstance(entry, dict) or not entry.get("content"):
        return None
    try:
        raw = base64.b64decode(entry["content"])
    except Exception:
        return None
    return raw.decode("utf-8", "replace")


def is_generated_workflow(workflow):
    """True for GitHub-managed workflows that maintainers did not author.

    Generated workflows live outside .github/workflows (for example
    dynamic/pages/pages-build-deployment) and must not be scored as CI.
    """
    path = (workflow or {}).get("path") or ""
    name = ((workflow or {}).get("name") or "").lower()
    if path and not path.startswith(".github/workflows/"):
        return True
    return "pages-build-deployment" in name or "pages-build-deployment" in path


def _has(names, *candidates):
    low = {n.lower() for n in names}
    for c in candidates:
        if c.lower() in low:
            return True
    return False


def _file_names(entries):
    return [e.get("name", "") for e in entries if isinstance(e, dict)]


def _dir_names(entries):
    return [
        e.get("name", "")
        for e in entries
        if isinstance(e, dict) and e.get("type") == "dir"
    ]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _pct(part, whole):
    return 0 if not whole else int(round(100.0 * part / whole))


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_days(value, now):
    dt = _parse_dt(value)
    return None if dt is None else (now - dt).days


def analyse(data):
    """Return (dimensions, findings).

    Each dimension: {"key","label","weight","score","checks":[...]}
    Each check: {"label","status":"pass|warn|fail|unknown","detail","points","max"}
    Each finding: {"severity","title","body","dimension"}
    """
    now = datetime.now(timezone.utc)
    repo = data["repo"]
    root_n = _file_names(data["root"])
    root_d = _dir_names(data["root"])
    gh_n = _file_names(data["dotgithub"])
    community = data.get("community") or {}
    files = community.get("files") or {}
    findings = []

    def finding(severity, dimension, title, body):
        findings.append(
            {
                "severity": severity,
                "dimension": dimension,
                "title": title,
                "body": body,
            }
        )

    # ---------------- Documentation (25) ----------------
    doc_checks = []

    readme = files.get("readme")
    if not readme and not _has(root_n, "README.md", "README.rst", "README.txt", "readme.md"):
        doc_checks.append(("README present", "fail", "No README file found at the repository root.", 0, 8))
        finding(
            "high",
            "Documentation",
            "Add a README with install, usage and contribution basics",
            "There is no README at the repository root. A first-time visitor cannot tell what "
            "the project does or how to run it.\n\n"
            "Suggested README outline:\n"
            "1. What this project does (one paragraph)\n"
            "2. Who it is for\n"
            "3. Install / quick start\n"
            "4. Usage examples\n"
            "5. How to run tests\n"
            "6. How to contribute and where to ask questions\n"
            "7. License",
        )
    else:
        readme_meta = api_safe_len(readme)
        points = 8 if readme_meta >= 400 else 5
        status = "pass" if readme_meta >= 400 else "warn"
        detail = "README present (%d bytes)" % readme_meta
        doc_checks.append(("README present", status, detail, points, 8))
        if readme_meta < 400:
            finding(
                "medium",
                "Documentation",
                "Expand the README so it explains setup, usage and tests",
                "The README exists but is very short. Visitors cannot reproduce a working setup "
                "from it. Add install steps, a usage example, and the test command.",
            )

    desc = (repo.get("description") or "").strip()
    doc_checks.append(
        (
            "Repository description",
            "pass" if desc else "fail",
            ("Description set: %r" % desc) if desc else "The repository has no description.",
            3 if desc else 0,
            3,
        )
    )
    if not desc:
        finding(
            "low",
            "Documentation",
            "Set a one-line repository description and topics",
            "The repository has no description. The description and topics are what GitHub "
            "search and topic listings use to surface the project, so leaving them blank "
            "removes free discovery.",
        )

    lic = files.get("license") or repo.get("license")
    doc_checks.append(
        (
            "License",
            "pass" if lic else "fail",
            "License detected" if lic else "No license file detected.",
            4 if lic else 0,
            4,
        )
    )
    if not lic:
        finding(
            "high",
            "Documentation",
            "Add a license so the project is legally usable",
            "No license file was detected. Without a license, the default is all rights "
            "reserved: companies and many contributors cannot legally use or contribute to "
            "the code. Pick one at https://choosealicense.com/ and add it as LICENSE.",
        )

    contributing = files.get("contributing")
    doc_checks.append(
        (
            "Contributing guide",
            "pass" if contributing else "warn",
            "CONTRIBUTING present" if contributing else "No CONTRIBUTING guide.",
            3 if contributing else 0,
            3,
        )
    )
    if not contributing:
        finding(
            "medium",
            "Documentation",
            "Add a CONTRIBUTING guide (or a contributing section in the README)",
            "There is no contribution guide. New contributors have to guess the branch, test "
            "and review conventions, which raises the drop-off rate on first PRs. A short "
            "guide is enough: setup, tests, commit style, how to ask for review.",
        )

    coc = files.get("code_of_conduct")
    doc_checks.append(
        (
            "Code of conduct",
            "pass" if coc else "warn",
            "Present" if coc else "No code of conduct.",
            2 if coc else 0,
            2,
        )
    )

    security = files.get("security") or "SECURITY.md" in root_n or "SECURITY.md" in gh_n
    doc_checks.append(
        (
            "Security policy",
            "pass" if security else "warn",
            "Present" if security else "No SECURITY.md; reports have no private route.",
            2 if security else 0,
            2,
        )
    )
    if not security:
        finding(
            "medium",
            "Documentation",
            "Add a SECURITY.md with a private vulnerability reporting route",
            "There is no security policy. Without one, security reports land in the public "
            "issue tracker. Add SECURITY.md naming a contact and, if available, enable "
            "GitHub private vulnerability reporting.",
        )

    doc_max = sum(c[4] for c in doc_checks)
    doc_points = sum(c[3] for c in doc_checks)
    doc_score = _pct(doc_points, doc_max)

    # ---------------- Testing (20) ----------------
    test_checks = []
    test_dirs = [
        d
        for d in root_d
        if _lower(d) in ("test", "tests", "spec", "specs", "__tests__", "t")
    ]
    test_files_root = [
        n
        for n in root_n
        if re.search(r"(^|[._-])test", _lower(n)) or _lower(n).startswith(("test_", "spec_"))
    ]
    test_found = bool(test_dirs or test_files_root)
    test_checks.append(
        (
            "Test suite present",
            "pass" if test_found else "fail",
            (
                "Found: %s" % ", ".join(test_dirs or test_files_root)
                if test_found
                else "No test directory or test file found."
            ),
            9 if test_found else 0,
            9,
        )
    )
    if not test_found:
        finding(
            "high",
            "Testing",
            "Add a minimal automated test suite and a way to run it",
            "No tests were detected. That makes every future change a gamble and makes "
            "reviewers verify behaviour by hand. Start with one happy-path test for the "
            "public interface and a documented command (for example `make test`) so CI and "
            "contributors run the same thing.",
        )

    framework_hits = [
        n
        for n in root_n
        if _lower(n)
        in (
            "pytest.ini",
            "tox.ini",
            "noxfile.py",
            "conftest.py",
            "jest.config.js",
            "jest.config.ts",
            "vitest.config.ts",
            "vitest.config.js",
            "phpunit.xml",
            "phpunit.xml.dist",
            "karma.conf.js",
        )
    ]
    has_framework = bool(framework_hits) or _has(root_n, "pyproject.toml", "package.json", "go.mod", "cargo.toml")
    test_checks.append(
        (
            "Test runner configured",
            "pass" if framework_hits else ("warn" if has_framework else "warn"),
            (
                "Config: %s" % ", ".join(framework_hits)
                if framework_hits
                else "Runner inferred from project manifest; no dedicated test config."
            ),
            6 if framework_hits else (3 if has_framework else 0),
            6,
        )
    )

    test_checks.append(
        (
            "CI runs tests",
            "pass" if _ci_runs_tests(data) else "fail",
            (
                "A workflow appears to run tests."
                if _ci_runs_tests(data)
                else "No workflow runs the test command."
            ),
            5 if _ci_runs_tests(data) else 0,
            5,
        )
    )
    if not _ci_runs_tests(data):
        finding(
            "high",
            "Testing",
            "Run the test suite in CI on every push and pull request",
            "Nothing in CI runs the tests, so regressions can merge unnoticed. Add a workflow "
            "triggered by `push` and `pull_request` that installs dependencies and runs the "
            "test command. Fail the job on non-zero exit so branch protection can depend on it.",
        )

    test_max = sum(c[4] for c in test_checks)
    test_points = sum(c[3] for c in test_checks)
    test_score = _pct(test_points, test_max)

    # ---------------- CI/CD (20) ----------------
    ci_checks = []
    workflows = data["workflows"]
    ci_checks.append(
        (
            "Workflows defined",
            "pass" if workflows else "fail",
            (
                "%d workflow(s): %s"
                % (len(workflows), ", ".join(w.get("name", "?") for w in workflows[:5]))
                if workflows
                else "No GitHub Actions workflows."
            ),
            8 if workflows else 0,
            8,
        )
    )
    if not workflows:
        finding(
            "high",
            "CI/CD",
            "Add a CI workflow (lint and test on push and pull request)",
            "There are no GitHub Actions workflows. Every quality check is manual today. A "
            "single `.github/workflows/ci.yml` that runs on `push` and `pull_request`, "
            "installs dependencies, lints and runs the tests is the highest-leverage "
            "addition, and it is what branch protection needs to block broken merges.",
        )

    triggers = workflow_triggers(data)
    push_pr = bool({"push", "pull_request", "pull_request_target"} & triggers)
    ci_checks.append(
        (
            "Runs on push/PR",
            "pass" if push_pr else ("fail" if workflows else "fail"),
            "Triggers: %s" % ", ".join(sorted(triggers)) if triggers else "No push/PR trigger found.",
            6 if push_pr else 0,
            6,
        )
    )
    if workflows and not push_pr:
        finding(
            "medium",
            "CI/CD",
            "Trigger CI on push and pull_request",
            "Workflows exist but none appear to run on `push` or `pull_request`, so pull "
            "requests are not verified.",
        )

    runs = [
        r
        for r in data["runs"]
        if (r.get("path") or "").startswith(".github/workflows/")
    ]
    failing = [r for r in runs if r.get("conclusion") == "failure"]
    recent_ok = [r for r in runs if r.get("conclusion") == "success"]
    if runs:
        ci_status = "warn" if failing and not recent_ok else "pass"
        ci_detail = "%d recent runs, %d success, %d failure" % (
            len(runs),
            len(recent_ok),
            len(failing),
        )
        ci_points = 4 if ci_status == "pass" else 2
    else:
        ci_status = "warn"
        ci_detail = "No workflow runs recorded."
        ci_points = 0
    ci_checks.append(("Workflow run health", ci_status, ci_detail, ci_points, 4))

    dep = data["has_dependabot"] or data["has_renovate"]
    ci_checks.append(
        (
            "Dependency updates automated",
            "pass" if dep else "warn",
            (
                "Dependabot" if data["has_dependabot"] else "Renovate"
            ) + " config found"
            if dep
            else "No Dependabot/Renovate config.",
            2 if dep else 0,
            2,
        )
    )
    if not dep:
        finding(
            "low",
            "CI/CD",
            "Enable automated dependency updates (Dependabot or Renovate)",
            "No dependency-update automation was found. Vulnerable or outdated pins then sit "
            "until someone notices. Dependabot is free on public repositories and needs only "
            "a small `.github/dependabot.yml`.",
        )

    ci_max = sum(c[4] for c in ci_checks)
    ci_points = sum(c[3] for c in ci_checks)
    ci_score = _pct(ci_points, ci_max)

    # ---------------- Issue triage (20) ----------------
    triage_checks = []
    open_items = data["open_issues"]
    real_issues = [i for i in open_items if "pull_request" not in i]
    labeled = [i for i in real_issues if i.get("labels")]
    label_coverage = _pct(len(labeled), len(real_issues)) if real_issues else None

    if real_issues:
        ok = label_coverage >= 60
        triage_checks.append(
            (
                "Open issues labeled",
                "pass" if ok else ("warn" if label_coverage >= 25 else "fail"),
                "%d/%d open issues have at least one label (%d%%)."
                % (len(labeled), len(real_issues), label_coverage),
                7 if ok else (4 if label_coverage >= 25 else 0),
                7,
            )
        )
        if not ok:
            finding(
                "medium",
                "Issue triage",
                "Label incoming issues so triage is possible at a glance",
                "Only %d of %d open issues carry a label. With no labels, nobody can filter "
                "bug versus feature versus question, or tell what is already triaged. Add a "
                "small label set (bug, enhancement, question, good first issue, "
                "needs-repro) and apply it on triage." % (len(labeled), len(real_issues)),
            )
    else:
        triage_checks.append(
            (
                "Open issues labeled",
                "warn",
                "No open issues to measure (0 open, nothing to triage).",
                0,
                7,
            )
        )

    templates = bool(files.get("issue_template")) or any(
        _lower(n) in ("issue_template.md", "issue_template.yml", "config.yml") for n in gh_n
    )
    pr_template = bool(files.get("pull_request_template")) or any(
        "pull_request_template" in _lower(n) for n in gh_n
    )
    triage_checks.append(
        (
            "Issue/PR templates",
            "pass" if (templates and pr_template) else ("warn" if templates or pr_template else "fail"),
            "Issue template: %s; PR template: %s"
            % ("yes" if templates else "no", "yes" if pr_template else "no"),
            6 if (templates and pr_template) else (3 if (templates or pr_template) else 0),
            6,
        )
    )
    if not (templates and pr_template):
        finding(
            "medium",
            "Issue triage",
            "Add issue and pull request templates",
            "Missing templates: %s. Templates ask for the version, steps to reproduce and "
            "expected behaviour up front, which removes a full round-trip on most reports."
            % ", ".join(
                [x for x, present in (("issue template", templates), ("PR template", pr_template)) if not present]
            ),
        )

    label_names = {_lower(l.get("name", "")) for l in data["labels"]}
    triage_labels = {
        "bug",
        "enhancement",
        "question",
        "documentation",
        "good first issue",
        "help wanted",
        "needs-repro",
        "duplicate",
        "wontfix",
        "invalid",
    }
    label_hits = sorted(label_names & triage_labels)
    triage_checks.append(
        (
            "Triage labels defined",
            "pass" if len(label_hits) >= 4 else ("warn" if label_hits else "fail"),
            "%d/%d common triage labels present: %s"
            % (len(label_hits), len(triage_labels), ", ".join(label_hits) or "none"),
            4 if len(label_hits) >= 4 else (2 if label_hits else 0),
            4,
        )
    )

    days_since_push = _age_days(repo.get("pushed_at"), now)
    triage_checks.append(
        (
            "Issue tracker enabled",
            "pass" if repo.get("has_issues") else "fail",
            "Issues enabled" if repo.get("has_issues") else "Issues are disabled.",
            3 if repo.get("has_issues") else 0,
            3,
        )
    )

    triage_max = sum(c[4] for c in triage_checks)
    triage_points = sum(c[3] for c in triage_checks)
    triage_score = _pct(triage_points, triage_max)

    # ---------------- Maintenance (15) ----------------
    maint_checks = []
    commits = data["commits"]
    last_commit_days = None
    if commits:
        last = commits[0].get("commit", {}).get("committer", {}).get("date")
        last_commit_days = _age_days(last, now)
    if last_commit_days is None:
        status, points, detail = "unknown", 3, "No commit dates available."
    elif last_commit_days <= 30:
        status, points, detail = "pass", 6, "Last commit %d day(s) ago." % last_commit_days
    elif last_commit_days <= 180:
        status, points, detail = "warn", 4, "Last commit %d day(s) ago." % last_commit_days
    else:
        status, points, detail = "fail", 0, "Last commit %d day(s) ago." % last_commit_days
    maint_checks.append(("Recent activity", status, detail, points, 6))
    if status == "fail":
        finding(
            "medium",
            "Maintenance",
            "Clarify project status or resume maintenance",
            "The last commit was %d days ago. Users cannot tell whether the project is "
            "maintained. Either resume regular commits or add a clear status note to the "
            "README (for example: seeking maintainers, or archived / unmaintained)."
            % last_commit_days,
        )

    tags = data["tags"]
    releases = data["releases"]
    has_release = bool(tags or releases)
    maint_checks.append(
        (
            "Tagged releases",
            "pass" if has_release else "warn",
            "%d tag(s), %d release(s)" % (len(tags), len(releases))
            if has_release
            else "No tags or releases found.",
            4 if has_release else 0,
            4,
        )
    )
    if not has_release:
        finding(
            "medium",
            "Maintenance",
            "Cut a tagged release so users can pin a version",
            "There are no tags or releases. Without a version to pin, downstream users track "
            "a moving default branch and cannot roll back. Tag the current stable commit and "
            "write short release notes.",
        )

    commits_90 = 0
    for c in commits:
        d = _age_days(c.get("commit", {}).get("committer", {}).get("date"), now)
        if d is not None and d <= 90:
            commits_90 += 1
    maint_checks.append(
        (
            "Commit frequency",
            "pass" if commits_90 >= 3 else ("warn" if commits_90 else "fail"),
            "%d commit(s) in the last 90 days (last %d sampled)." % (commits_90, len(commits)),
            3 if commits_90 >= 3 else (1 if commits_90 else 0),
            3,
        )
    )

    archived = repo.get("archived")
    maint_checks.append(
        (
            "Not archived",
            "pass" if not archived else "fail",
            "Active" if not archived else "Repository is archived.",
            2 if not archived else 0,
            2,
        )
    )

    maint_max = sum(c[4] for c in maint_checks)
    maint_points = sum(c[3] for c in maint_checks)
    maint_score = _pct(maint_points, maint_max)

    dimensions = [
        ("documentation", "Documentation", 25, doc_score, doc_checks),
        ("testing", "Testing", 20, test_score, test_checks),
        ("ci", "CI/CD", 20, ci_score, ci_checks),
        ("triage", "Issue triage", 20, triage_score, triage_checks),
        ("maintenance", "Maintenance", 15, maint_score, maint_checks),
    ]

    total = int(round(sum(w * s for _, _, w, s, _ in dimensions) / 100.0))
    return dimensions, findings, total


def api_safe_len(entry):
    """Community file objects include `size` on some responses; fall back to 0."""
    if not isinstance(entry, dict):
        return 0
    return int(entry.get("size") or 0)


def _ci_runs_tests(data):
    """Does an authored workflow actually invoke a test command?"""
    text = " ".join(data.get("workflow_files", {}).values()).lower()
    if not text:
        return False
    return bool(
        re.search(
            r"\b(pytest|unittest|jest|vitest|mocha|phpunit|go test|cargo test|"
            r"npm (run )?test|yarn test|pnpm test|tox|nox|rspec|rake test|"
            r"dotnet test|gradle test|mvn test)\b",
            text,
        )
    )


def workflow_triggers(data):
    """Parse real `on:` triggers out of the fetched workflow files."""
    triggers = set()
    for content in data.get("workflow_files", {}).values():
        # Matches both `on: push` and a mapping block `on:\n  push:`
        for m in re.finditer(r"^\s*on\s*:\s*(.*)$", content, flags=re.M | re.I):
            rest = (m.group(1) or "").strip()
            if rest:
                for tok in re.split(r"[,\[\] ]+", rest):
                    tok = tok.strip().strip("'\"")
                    if tok:
                        triggers.add(tok)
        for m in re.finditer(
            r"^\s*(push|pull_request_target|pull_request|workflow_dispatch|"
            r"workflow_call|schedule|release)\s*:",
            content,
            flags=re.M,
        ):
            triggers.add(m.group(1))
    return triggers


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEVERITY_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}


def band(score):
    if score >= 85:
        return "Strong"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Needs work"
    return "At risk"


def render_report(full_name, data, dimensions, findings, total, generated):
    repo = data["repo"]
    out = []
    out.append("# Repository health audit — `%s`" % full_name)
    out.append("")
    out.append(
        "**Overall score: %d / 100 — %s**  " % (total, band(total))
    )
    out.append(
        "_Generated %s from the public GitHub REST API. Read-only: no change was made "
        "to the repository._" % generated
    )
    out.append("")
    out.append("## At a glance")
    out.append("")
    out.append("| Dimension | Weight | Score |")
    out.append("|---|---|---|")
    for key, label, weight, score, _ in dimensions:
        out.append("| %s | %d%% | %d/100 |" % (label, weight, score))
    out.append("")
    out.append("## Repository facts")
    out.append("")
    out.append("| Field | Value |")
    out.append("|---|---|")
    facts = [
        ("Description", repo.get("description") or "_(none)_"),
        ("Primary language", repo.get("language") or "_(unknown)_"),
        ("Stars / forks", "%s / %s" % (repo.get("stargazers_count"), repo.get("forks_count"))),
        ("Open issues (incl. PRs)", repo.get("open_issues_count")),
        ("Default branch", repo.get("default_branch")),
        ("License", (repo.get("license") or {}).get("spdx_id") or "_(none)_"),
        ("Created", (repo.get("created_at") or "")[:10]),
        ("Last push", (repo.get("pushed_at") or "")[:10]),
        ("Archived", "yes" if repo.get("archived") else "no"),
    ]
    for label, value in facts:
        out.append("| %s | %s |" % (label, value))
    out.append("")
    out.append("## Findings (most important first)")
    out.append("")
    if not findings:
        out.append("No gaps found by the checks in this audit.")
    for i, f in enumerate(sorted(findings, key=lambda x: SEVERITY_ORDER.get(x["severity"], 9)), 1):
        out.append("### %d. [%s] %s" % (i, SEVERITY_LABEL[f["severity"]], f["title"]))
        out.append("")
        out.append("_Area: %s_" % f["dimension"])
        out.append("")
        out.append(f["body"])
        out.append("")
    out.append("## Dimension detail")
    out.append("")
    for key, label, weight, score, checks in dimensions:
        out.append("### %s — %d/100" % (label, score))
        out.append("")
        out.append("| Check | Result | Detail | Points |")
        out.append("|---|---|---|---|")
        for cname, cstatus, cdetail, cpoints, cmax in checks:
            icon = {"pass": "pass", "warn": "warn", "fail": "fail", "unknown": "n/a"}[cstatus]
            out.append("| %s | %s | %s | %d/%d |" % (cname, icon, cdetail, cpoints, cmax))
        out.append("")
    out.append("## Method")
    out.append("")
    out.append(
        "Five weighted dimensions: Documentation 25, Testing 20, CI/CD 20, Issue triage 20, "
        "Maintenance 15. Every check reads public data available to anyone with the GitHub "
        "API; nothing is inferred from private sources. A `warn` result means the signal is "
        "absent or weak, `fail` means the practice is missing, `n/a` means GitHub returned no "
        "data to measure."
    )
    out.append("")
    out.append("_Generated by an AI agent (OpenHands) on behalf of the user._")
    return "\n".join(out)


def render_issues(full_name, findings):
    out = ["# Ready-to-file issue checklist — `%s`" % full_name, ""]
    out.append(
        "One entry per finding. Copy the title and body into a new issue, or use "
        "`gh issue create`."
    )
    out.append("")
    for i, f in enumerate(sorted(findings, key=lambda x: SEVERITY_ORDER.get(x["severity"], 9)), 1):
        out.append("## %d. %s" % (i, f["title"]))
        out.append("")
        out.append("- Severity: %s" % SEVERITY_LABEL[f["severity"]])
        out.append("- Area: %s" % f["dimension"])
        out.append("- Suggested labels: `%s`" % suggested_labels(f))
        out.append("")
        out.append("```")
        out.append(f["body"])
        out.append("```")
        out.append("")
    return "\n".join(out)


def suggested_labels(f):
    area = f["dimension"].lower()
    labels = []
    if area == "documentation":
        labels.append("documentation")
    elif area == "testing":
        labels.append("testing")
    elif area == "ci/cd":
        labels.append("ci")
    elif area == "issue triage":
        labels.append("triage")
    else:
        labels.append("maintenance")
    if f["severity"] == "high":
        labels.append("bug")
    labels.append("good first issue")
    return ", ".join(dict.fromkeys(labels))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only open-source repo health audit.")
    ap.add_argument("repo", help="owner/name of a public GitHub repository")
    ap.add_argument("--out", help="write the Markdown report here")
    ap.add_argument("--issues", help="write the issue checklist here")
    ap.add_argument("--json", action="store_true", help="print a JSON summary")
    args = ap.parse_args(argv)

    if "/" not in args.repo:
        raise SystemExit("Expected owner/name, got %r" % args.repo)

    api = Api(token())
    data = collect(api, args.repo)
    dimensions, findings, total = analyse(data)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = render_report(args.repo, data, dimensions, findings, total, generated)
    issues = render_issues(args.repo, findings)

    if args.json:
        print(
            json.dumps(
                {
                    "repo": args.repo,
                    "score": total,
                    "band": band(total),
                    "dimensions": {k: s for k, _, _, s, _ in dimensions},
                    "findings": len(findings),
                    "api_requests": api.requests,
                },
                indent=2,
            )
        )
    else:
        print(report)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print("\n[report written to %s]" % args.out, file=sys.stderr)
    if args.issues:
        with open(args.issues, "w", encoding="utf-8") as fh:
            fh.write(issues + "\n")
        print("[issue checklist written to %s]" % args.issues, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
