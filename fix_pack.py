#!/usr/bin/env python3
"""Turn a repo health audit into a ready-to-commit fix pack.

Read-only: consumes the JSON output of `repo_audit.py --json` and writes
suggested files (issue templates, PR template, CONTRIBUTING, CI workflow,
dependabot config, housekeeping issue drafts). It never reads or writes the
audited repository.
"""

import argparse
import json
import os
import subprocess
import sys

from repo_audit import Api, analyse, band, collect, token

# ---------------------------------------------------------------------------
# Language / tooling detection
# ---------------------------------------------------------------------------

# Ordered most-specific first; the first match wins.
LANGUAGE_TABLE = [
    ("javascript", "file", "package.json", "node", "npm test"),
    ("typescript", "file", "package.json", "node", "npm test"),
    ("python", "file", "pyproject.toml", "python", "python -m pytest"),
    ("python", "file", "pytest.ini", "python", "python -m pytest"),
    ("python", "file", "tox.ini", "python", "python -m tox"),
    ("python", "file", "requirements.txt", "python", "python -m pytest"),
    ("python", "file", "setup.py", "python", "python -m pytest"),
    ("go", "file", "go.mod", "go", "go test ./..."),
    ("rust", "file", "Cargo.toml", "rust", "cargo test"),
    ("ruby", "file", "Gemfile", "ruby", "bundle exec rspec"),
    ("java", "file", "pom.xml", "java", "mvn -B test"),
    ("java", "file", "build.gradle", "java", "gradle test"),
    ("java", "file", "build.gradle.kts", "java", "gradle test"),
    ("php", "file", "composer.json", "php", "composer test"),
    ("dotnet", "file", "global.json", "dotnet", "dotnet test"),
]

SETUP_STEPS = {
    "node": ("      - uses: actions/setup-node@v4\n"
             "        with:\n"
             "          node-version: \"20\""),
    "python": ("      - uses: actions/setup-python@v5\n"
               "        with:\n"
               "          python-version: \"3.x\""),
    "go": ("      - uses: actions/setup-go@v5\n"
           "        with:\n"
           "          go-version: \"stable\""),
    "rust": ("      - uses: dtolnay/rust-toolchain@stable"),
    "java": ("      - uses: actions/setup-java@v4\n"
             "        with:\n"
             "          distribution: temurin\n"
             "          java-version: \"21\""),
    "ruby": ("      - uses: ruby/setup-ruby@v1\n"
             "        with:\n"
             "          bundler-cache: true"),
    "php": ("      - uses: shivammathur/setup-php@v2\n"
            "        with:\n"
            "          php-version: \"8.3\""),
    "dotnet": ("      - uses: actions/setup-dotnet@v4\n"
               "        with:\n"
               "          dotnet-version: \"8.0.x\""),
}


def detect_tooling(data):
    """Return (key, runner, install_cmd, test_cmd, setup_yaml, markdown_label)."""
    names = {
        (e.get("name") or "").lower()
        for e in (data.get("root") or [])
        if isinstance(e, dict)
    }
    lang = (data["repo"].get("language") or "").lower()

    for _label, kind, marker, runner, test_cmd in LANGUAGE_TABLE:
        if kind == "file" and marker.lower() in names:
            return _package(runner, test_cmd)
    if lang == "python":
        return _package("python", "python -m pytest")
    if lang in ("javascript", "typescript"):
        return _package("node", "npm test")
    if lang == "go":
        return _package("go", "go test ./...")
    if lang == "rust":
        return _package("rust", "cargo test")
    if lang == "ruby":
        return _package("ruby", "bundle exec rspec")
    if lang in ("java", "kotlin"):
        return _package("java", "mvn -B test")
    if lang == "php":
        return _package("php", "composer test")
    if lang in ("c#", "csharp", "f#"):
        return _package("dotnet", "dotnet test")
    return None


def _package(runner, test_cmd):
    install = {
        "node": "if [ -f package-lock.json ]; then npm ci; else npm install; fi",
        "python": "python -m pip install --upgrade pip\n"
                  "          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi\n"
                  "          if [ -f pyproject.toml ]; then pip install -e . 2>/dev/null || true; fi",
        "go": "go mod download",
        "rust": "",
        "java": "",
        "ruby": "bundle install",
        "php": "composer install --no-interaction --prefer-dist",
        "dotnet": "",
    }[runner]
    return {
        "runner": runner,
        "install": install,
        "test": test_cmd,
        "setup": SETUP_STEPS[runner],
    }


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def tpl_issue_bug():
    return (
        "name: Bug report\n"
        "description: Something is not working as expected\n"
        "title: \"[Bug]: \"\n"
        "labels: [\"bug\", \"needs triage\"]\n"
        "body:\n"
        "  - type: markdown\n"
        "    attributes:\n"
        "      value: |\n"
        "        Thanks for taking the time to file a bug. Please fill this in so it can be\n"
        "        reproduced and fixed quickly.\n"
        "  - type: textarea\n"
        "    id: what-happened\n"
        "    attributes:\n"
        "      label: What happened?\n"
        "      description: A clear description of the bug, including the exact error output.\n"
        "      placeholder: When I run X, I see Y instead of Z.\n"
        "    validations:\n"
        "      required: true\n"
        "  - type: textarea\n"
        "    id: reproduce\n"
        "    attributes:\n"
        "      label: Steps to reproduce\n"
        "      placeholder: |\n"
        "        1. Run ...\n"
        "        2. Call ...\n"
        "        3. Observe ...\n"
        "    validations:\n"
        "      required: true\n"
        "  - type: input\n"
        "    id: version\n"
        "    attributes:\n"
        "      label: Version or commit\n"
        "      description: The release tag or commit hash you are running.\n"
        "    validations:\n"
        "      required: true\n"
        "  - type: dropdown\n"
        "    id: os\n"
        "    attributes:\n"
        "      label: Operating system\n"
        "      options:\n"
        "        - Linux\n"
        "        - macOS\n"
        "        - Windows\n"
        "        - Other\n"
        "    validations:\n"
        "      required: false\n"
        "  - type: textarea\n"
        "    id: expected\n"
        "    attributes:\n"
        "      label: Expected behaviour\n"
        "    validations:\n"
        "      required: false\n"
    )


def tpl_issue_feature():
    return (
        "name: Feature request\n"
        "description: Suggest an idea or improvement\n"
        "title: \"[Feature]: \"\n"
        "labels: [\"enhancement\", \"needs triage\"]\n"
        "body:\n"
        "  - type: textarea\n"
        "    id: problem\n"
        "    attributes:\n"
        "      label: What problem does this solve?\n"
        "      description: Describe the problem rather than the implementation.\n"
        "    validations:\n"
        "      required: true\n"
        "  - type: textarea\n"
        "    id: proposal\n"
        "    attributes:\n"
        "      label: Proposed solution\n"
        "    validations:\n"
        "      required: false\n"
        "  - type: textarea\n"
        "    id: alternatives\n"
        "    attributes:\n"
        "      label: Alternatives considered\n"
        "    validations:\n"
        "      required: false\n"
    )


def tpl_issue_config():
    return (
        "# https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository\n"
        "blank_issues_enabled: false\n"
        "contact_links:\n"
        "  - name: Question or support\n"
        "    url: https://github.com/{repo}/discussions\n"
        "    about: Ask a question or start a discussion\n"
    )


def tpl_pr():
    return (
        "<!-- Please keep the description focused on what changed and why. -->\n"
        "\n"
        "## What changed\n"
        "\n"
        "-\n"
        "\n"
        "## Why\n"
        "\n"
        "<!-- Link the issue: Closes #123 -->\n"
        "\n"
        "## How it was tested\n"
        "\n"
        "- [ ] Added or updated tests\n"
        "- [ ] Ran the test suite locally\n"
        "\n"
        "## Checklist\n"
        "\n"
        "- [ ] Documentation updated where behaviour changed\n"
        "- [ ] No secrets or credentials committed\n"
    )


def tpl_contributing(test_cmd):
    return (
        "# Contributing\n"
        "\n"
        "Thanks for taking the time to contribute.\n"
        "\n"
        "## Getting set up\n"
        "\n"
        "```bash\n"
        "# clone your fork, then\n"
        "git checkout -b my-change\n"
        "```\n"
        "\n"
        "## Running the tests\n"
        "\n"
        "```bash\n"
        "{test_cmd}\n"
        "```\n"
        "\n"
        "Please run the tests before opening a pull request and add a test for any\n"
        "behaviour change.\n"
        "\n"
        "## Submitting a pull request\n"
        "\n"
        "1. Keep each pull request focused on one change.\n"
        "2. Describe what changed and why.\n"
        "3. Link the issue it resolves (for example `Closes #12`).\n"
        "\n"
        "## Reporting bugs and requesting features\n"
        "\n"
        "Use the issue templates. A minimal reproduction or a clear problem statement\n"
        "makes the difference between a fix this week and a fix next quarter.\n"
        "\n"
        "## Code of conduct\n"
        "\n"
        "Be kind and assume good faith. Detailed expectations can be added in a\n"
        "`CODE_OF_CONDUCT.md` (see the Contributor Covenant).\n"
        "\n"
        "_Generated by an AI agent (OpenHands) on behalf of the user._\n"
    ).format(test_cmd=test_cmd)


def tpl_security():
    return (
        "# Security policy\n"
        "\n"
        "## Reporting a vulnerability\n"
        "\n"
        "Please do not open a public issue for security problems. Instead use GitHub's\n"
        "[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)\n"
        "(Security > Advisories > Report a vulnerability) on this repository.\n"
        "\n"
        "Include the affected version, a reproduction, and the impact you believe it has.\n"
        "You can expect an acknowledgement within a few days.\n"
        "\n"
        "_Generated by an AI agent (OpenHands) on behalf of the user._\n"
    )


def tpl_ci(repo, tooling):
    lineno = 0
    steps = []
    steps.append(tooling["setup"])
    if tooling["install"]:
        steps.append("      - name: Install dependencies\n        run: |\n          " + tooling["install"])
    steps.append("      - name: Run tests\n        run: " + tooling["test"])
    body = "\n".join(steps)
    return (
        "# Generated from a read-only repo health audit of {repo}.\n"
        "# Review the test command before merging: it is a starting point, not a\n"
        "# guarantee that the project's test runner is configured exactly this way.\n"
        "name: CI\n"
        "\n"
        "on:\n"
        "  push:\n"
        "    branches: [main, master]\n"
        "  pull_request:\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "{body}\n"
    ).format(repo=repo, body=body)


TEST_SCAFFOLD = {
    "python": (
        "tests/test_smoke.py",
        '"""A first smoke test: replace with a real assertion about this project."""\n'
        "\n"
        "\n"
        "def test_importable():\n"
        "    assert True\n"
    ),
    "node": (
        "test/smoke.test.js",
        "// A first smoke test: replace with a real assertion about this project.\n"
        "const test = require(\"node:test\");\n"
        "const assert = require(\"node:assert\");\n"
        "\n"
        "test(\"smoke\", () => {\n"
        "  assert.ok(true);\n"
        "});\n"
    ),
    "go": (
        "smoke_test.go",
        "package main\n"
        "\n"
        "import \"testing\"\n"
        "\n"
        "// Replace with a real assertion about this project.\n"
        "func TestSmoke(t *testing.T) {\n"
        "\tif false {\n"
        "\t\tt.Fatal(\"unreachable\")\n"
        "\t}\n"
        "}\n"
    ),
    "rust": (
        "tests/smoke.rs",
        "// A first smoke test: replace with a real assertion about this project.\n"
        "#[test]\n"
        "fn smoke() {\n"
        "    assert!(true);\n"
        "}\n"
    ),
    "ruby": (
        "spec/smoke_spec.rb",
        "# A first smoke test: replace with a real assertion about this project.\n"
        "RSpec.describe \"smoke\" do\n"
        "  it \"passes\" do\n"
        "    expect(true).to eq(true)\n"
        "  end\n"
        "end\n"
    ),
    "java": (
        "src/test/java/SmokeTest.java",
        "import org.junit.jupiter.api.Test;\n"
        "import static org.junit.jupiter.api.Assertions.assertTrue;\n"
        "\n"
        "class SmokeTest {\n"
        "    @Test\n"
        "    void smoke() {\n"
        "        assertTrue(true);\n"
        "    }\n"
        "}\n"
    ),
}


def tpl_test_scaffold(runner):
    return TEST_SCAFFOLD.get(runner)


def tpl_testing_note(test_cmd):
    return (
        "# Testing\n"
        "\n"
        "No automated test suite was detected. This note is a starting point.\n"
        "\n"
        "## First test\n"
        "\n"
        "Add one happy-path test for the smallest public interface in the project.\n"
        "One real test that runs is worth more than a large suite that does not.\n"
        "\n"
        "## Wiring it up\n"
        "\n"
        "1. Pick the test runner for the project's language.\n"
        "2. Add a single test file next to the code it covers.\n"
        "3. Add the test command to the README and to CI:\n"
        "\n"
        "```bash\n"
        "{test_cmd}\n"
        "```\n"
        "\n"
        "## Why it matters\n"
        "\n"
        "Without tests, every change is verified by hand and reviewers have to reason\n"
        "about behaviour from the diff alone. A first smoke test turns CI into a real\n"
        "signal and gives contributors a safe place to add coverage.\n"
        "\n"
        "_Generated by an AI agent (OpenHands) on behalf of the user._\n"
    ).format(test_cmd=test_cmd)


def tpl_ci_generic(repo):
    return (
        "# Generated from a read-only repo health audit of {repo}.\n"
        "# Replace the placeholder command with the project's real test command.\n"
        "name: CI\n"
        "\n"
        "on:\n"
        "  push:\n"
        "    branches: [main, master]\n"
        "  pull_request:\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Run tests\n"
        "        run: |\n"
        "          echo \"No test command was detected. Replace this with the real one.\"\n"
        "          exit 1\n"
    ).format(repo=repo)


def tpl_dependabot(ecosystems):
    lines = [
        "# Generated from a read-only repo health audit.",
        "version: 2",
        "updates:",
    ]
    if not ecosystems:
        ecosystems = ["github-actions"]
    for eco in ecosystems:
        lines += [
            "  - package-ecosystem: \"%s\"" % eco,
            "    directory: \"/\"",
            "    schedule:",
            "      interval: \"weekly\"",
        ]
    return "\n".join(lines) + "\n"


def tpl_license_notice(findings):
    body = (
        "# Licensing\n"
        "\n"
        "This repository has no license file, which means the default is all rights\n"
        "reserved: companies and many contributors cannot legally use or reuse the code.\n"
        "\n"
        "Pick one and add it as `LICENSE` in the repository root. Two common choices:\n"
        "\n"
        "- **MIT** — short and permissive. <https://choosealicense.com/licenses/mit/>\n"
        "- **Apache-2.0** — permissive with an explicit patent grant.\n"
        "  <https://choosealicense.com/licenses/apache-2.0/>\n"
        "\n"
        "Then add the SPDX identifier to your package manifest and a short mention in\n"
        "the README. GitHub detects the license automatically once the file is present.\n"
        "\n"
        "_Generated by an AI agent (OpenHands) on behalf of the user._\n"
    )
    return body


def tpl_readme_skeleton(data):
    repo = data["repo"]
    name = repo.get("full_name") or repo.get("name") or "project"
    desc = (repo.get("description") or "One paragraph on what this project does and who it is for.").strip()
    test = (detect_tooling(data) or {}).get("test") or "TODO: add the project's test command"
    return (
        "# {name}\n"
        "\n"
        "{desc}\n"
        "\n"
        "## Install\n"
        "\n"
        "```bash\n"
        "# TODO: installation steps\n"
        "```\n"
        "\n"
        "## Usage\n"
        "\n"
        "```bash\n"
        "# TODO: a minimal working example\n"
        "```\n"
        "\n"
        "## Tests\n"
        "\n"
        "```bash\n"
        "{test}\n"
        "```\n"
        "\n"
        "## Contributing\n"
        "\n"
        "See [CONTRIBUTING.md](CONTRIBUTING.md). Please use the issue templates for\n"
        "bugs and feature requests.\n"
        "\n"
        "## License\n"
        "\n"
        "TODO: state the license (see the `LICENSE` file once added).\n"
    ).format(name=name, desc=desc, test=test)


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------

DOCUMENTATION_TITLES = {
    "Add issue and pull request templates": "_templates",
    "Add a CONTRIBUTING guide (or a contributing section in the README)": "_contributing",
    "Add a README with install, usage and contribution basics": "_readme",
    "Expand the README so it explains setup, usage and tests": "_readme",
    "Add a SECURITY.md with a private vulnerability reporting route": "_security",
    "Add a license so the project is legally usable": "_license",
}

CI_TITLES = {
    "Add a CI workflow (lint and test on push and pull request)": "workflow",
    "Run the test suite in CI on every push and pull request": "workflow",
    "Trigger CI on push and pull_request": "workflow",
    "Add a minimal automated test suite and a way to run it": "test_scaffold",
    "Enable automated dependency updates (Dependabot or Renovate)": "dependabot",
}

ECOSYSTEM_BY_RUNNER = {
    "node": "npm",
    "python": "pip",
    "go": "gomod",
    "rust": "cargo",
    "java": "maven",
    "ruby": "bundler",
    "php": "composer",
    "dotnet": "nuget",
}


def build_pack(full_name, data, findings, tooling):
    files = {}

    wanted = set()
    for f in findings:
        title = f.get("title")
        if title in DOCUMENTATION_TITLES:
            wanted.add(DOCUMENTATION_TITLES[title])
        if title in CI_TITLES:
            wanted.add(CI_TITLES[title])

    if "_templates" in wanted:
        files[".github/ISSUE_TEMPLATE/bug_report.yml"] = tpl_issue_bug()
        files[".github/ISSUE_TEMPLATE/feature_request.yml"] = tpl_issue_feature()
        files[".github/ISSUE_TEMPLATE/config.yml"] = tpl_issue_config().format(repo=full_name)
        files[".github/pull_request_template.md"] = tpl_pr()

    if "_contributing" in wanted:
        test_cmd = (tooling or {}).get("test") or "TODO: add the test command"
        files["CONTRIBUTING.md"] = tpl_contributing(test_cmd)

    if "_security" in wanted:
        files["SECURITY.md"] = tpl_security()

    if "_readme" in wanted:
        files["README.suggested.md"] = tpl_readme_skeleton(data)

    if "_license" in wanted:
        files["LICENSING.suggested.md"] = tpl_license_notice(findings)

    if "workflow" in wanted:
        if tooling:
            files[".github/workflows/" + _ci_name(tooling["runner"])] = tpl_ci(full_name, tooling)
        else:
            files[".github/workflows/ci.yml"] = tpl_ci_generic(full_name)

    if "dependabot" in wanted:
        eco = ECOSYSTEM_BY_RUNNER.get((tooling or {}).get("runner"))
        files[".github/dependabot.yml"] = tpl_dependabot([e for e in [eco] if e])

    if "test_scaffold" in wanted:
        scaffold = tpl_test_scaffold((tooling or {}).get("runner"))
        if scaffold:
            files[scaffold[0]] = scaffold[1]
        else:
            test_cmd = (tooling or {}).get("test") or "TODO: add the test command"
            files["TESTING.suggested.md"] = tpl_testing_note(test_cmd)

    handled_file_titles = set(DOCUMENTATION_TITLES) | set(CI_TITLES)
    issues = []
    for f in findings:
        if f.get("title") in handled_file_titles:
            continue
        issues.append(
            {
                "title": f["title"],
                "severity": f["severity"],
                "area": f["dimension"],
                "body": f["body"],
            }
        )
    return files, issues


def _ci_name(runner):
    return {
        "node": "ci.yml",
        "python": "ci.yml",
        "go": "ci.yml",
        "rust": "ci.yml",
        "java": "ci.yml",
        "ruby": "ci.yml",
        "php": "ci.yml",
        "dotnet": "ci.yml",
    }.get(runner, "ci.yml")


def render_pack_index(full_name, score, files, issues, tooling):
    out = ["# Fix pack — `%s`" % full_name, ""]
    out.append("**Audit score: %d / 100 — %s**" % (score, band(score)))
    out.append("")
    out.append(
        "This pack turns the audit findings into files you can review and commit. "
        "Nothing here has been applied to your repository."
    )
    out.append("")
    out.append("## Suggested files")
    out.append("")
    if files:
        out.append("| File | What it does |")
        out.append("|---|---|")
        for path in sorted(files):
            out.append("| `%s` | %s |" % (path, _purpose(path)))
    else:
        out.append("_No file-level fixes were implied by the findings._")
    out.append("")
    if tooling:
        out.append(
            "Detected tooling: `%s`, test command `%s`. Verify the command before "
            "merging the workflow." % (tooling["runner"], tooling["test"])
        )
    else:
        out.append(
            "No test runner was detected, so the workflow ships with a placeholder "
            "command. Replace it with the project's real test command."
        )
    out.append("")
    out.append("## Issues to file (not covered by a file above)")
    out.append("")
    if issues:
        for i, iss in enumerate(issues, 1):
            out.append("### %d. [%s] %s" % (i, iss["severity"].title(), iss["title"]))
            out.append("")
            out.append(iss["body"])
            out.append("")
    else:
        out.append("_None._")
    out.append("")
    out.append("## How to apply")
    out.append("")
    out.append("```bash")
    out.append("# from a clean branch in your repository")
    out.append("git checkout -b repo-health-fixes")
    out.append("git apply path/to/fix-pack.patch   # or copy the files in manually")
    out.append("git commit -m \"Add CI, issue templates and contributing guide\"")
    out.append("```")
    out.append("")
    out.append(
        "_Generated by an AI agent (OpenHands) on behalf of the user. Review every "
        "file before committing; generated configuration is a starting point._"
    )
    out.append("")
    return "\n".join(out)


def _purpose(path):
    return {
        ".github/ISSUE_TEMPLATE/bug_report.yml": "Structured bug reports (version, repro, expected)",
        ".github/ISSUE_TEMPLATE/feature_request.yml": "Structured feature requests",
        ".github/ISSUE_TEMPLATE/config.yml": "Turns off blank issues, adds a support link",
        ".github/pull_request_template.md": "PR checklist so reviews are consistent",
        ".github/workflows/ci.yml": "Runs the test suite on push and pull request",
        ".github/dependabot.yml": "Weekly dependency update PRs",
        "CONTRIBUTING.md": "Setup, test command and PR expectations",
        "SECURITY.md": "Private vulnerability reporting route",
        "README.suggested.md": "README skeleton with install, usage and tests",
        "LICENSING.suggested.md": "How to choose and add a license",
    }.get(path, "Suggested file from the audit")


# ---------------------------------------------------------------------------
# Bundling
# ---------------------------------------------------------------------------

def bundle_files(files):
    """Return concatenated Markdown of every suggested file."""
    out = []
    for path in sorted(files):
        out.append("## `%s`" % path)
        out.append("")
        lang = "yaml" if path.endswith((".yml", ".yaml")) else "markdown"
        out.append("```%s" % lang)
        out.append(files[path].rstrip("\n"))
        out.append("```")
        out.append("")
    return "\n".join(out)


def write_tree(outdir, files):
    for path, content in files.items():
        dest = os.path.join(outdir, path)
        os.makedirs(os.path.dirname(dest) or outdir, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
    return sorted(files)


def write_patch(path, files):
    """Write a unified diff (git apply compatible) without needing a git repo."""
    import difflib

    parts = []
    for name in sorted(files):
        old = ""
        new = files[name]
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile="b/" + name,
        )
        header = "diff --git a/%s b/%s\nnew file mode 100644\n" % (name, name)
        parts.append(header + "".join(diff))
    text = "".join(parts)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def describe(data, findings, tooling):
    return {
        "tooling": tooling["runner"] if tooling else None,
        "test_command": tooling["test"] if tooling else None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Turn a read-only repo health audit into a ready-to-commit fix pack."
    )
    ap.add_argument("repo", help="owner/name of a public GitHub repository")
    ap.add_argument(
        "--from-json",
        help="reuse a previous repo_audit.py JSON dump (the full `collect` structure, "
        "not the summary printed by `--json`) instead of querying the API",
    )
    ap.add_argument("--outdir", default="fix-pack", help="directory to write suggested files into")
    ap.add_argument("--index", default="FIX-PACK.md", help="write the explanatory index here")
    ap.add_argument("--pack", default="fix-pack.md", help="write the concatenated file bundle here")
    ap.add_argument("--patch", default="fix-pack.patch", help="write a git-apply compatible patch here")
    ap.add_argument("--json", action="store_true", help="print a JSON summary")
    args = ap.parse_args(argv)

    if "/" not in args.repo:
        raise SystemExit("Expected owner/name, got %r" % args.repo)

    if args.from_json:
        try:
            data = json.load(open(args.from_json, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit("Could not read %s: %s" % (args.from_json, exc))
        if not isinstance(data, dict) or "repo" not in data:
            raise SystemExit(
                "%s is not a full audit dump. Use `repo_audit.py` with a JSON dump of "
                "the collected data, not the `--json` summary." % args.from_json
            )
        dimensions, findings, score = analyse(data)
    else:
        data = collect(Api(token()), args.repo)
        dimensions, findings, score = analyse(data)

    tooling = detect_tooling(data)
    files, issues = build_pack(args.repo, data, findings, tooling)

    index = render_pack_index(args.repo, score, files, issues, tooling)
    with open(args.index, "w", encoding="utf-8") as fh:
        fh.write(index + "\n")

    with open(args.pack, "w", encoding="utf-8") as fh:
        fh.write(bundle_files(files) + "\n")

    write_tree(args.outdir, files)
    write_patch(args.patch, files)

    if args.json:
        print(json.dumps({
            "repo": args.repo,
            "score": score,
            "tooling": describe(data, findings, tooling),
            "files": sorted(files),
            "issues": [i["title"] for i in issues],
        }, indent=2))
    else:
        print(index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
