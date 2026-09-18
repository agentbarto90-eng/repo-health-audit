#!/usr/bin/env python3
"""Unit tests for the audit tool.

Run with:  python3 -m unittest -v test_repo_audit

These cover the pure logic and the free/paid boundary. They never touch the
network, so they run anywhere and do not need a GitHub token.
"""

import unittest

import repo_audit as ra
import run_audit_request as rar


def finding(title, severity="high", dimension="Documentation", body="Fix it."):
    return {"title": title, "severity": severity, "dimension": dimension, "body": body}


def dimensions():
    checks = [("A check", "pass", "looks fine", 10, 10)]
    return [
        ("documentation", "Documentation", 25, 40, checks),
        ("testing", "Testing", 20, 0, checks),
        ("ci", "CI/CD", 20, 60, checks),
        ("triage", "Issue triage", 20, 35, checks),
        ("maintenance", "Maintenance", 15, 73, checks),
    ]


def repo_data():
    return {
        "repo": {
            "description": "A repo",
            "language": "Python",
            "stargazers_count": 0,
            "forks_count": 0,
            "open_issues_count": 0,
            "default_branch": "main",
            "license": None,
            "created_at": "2020-01-01T00:00:00Z",
            "pushed_at": "2024-01-01T00:00:00Z",
            "archived": False,
        }
    }


class TestGeneratedWorkflow(unittest.TestCase):
    def test_authored_workflow_is_not_generated(self):
        self.assertFalse(ra.is_generated_workflow(
            {"path": ".github/workflows/ci.yml", "name": "CI"}))

    def test_pages_workflow_outside_workflows_dir_is_generated(self):
        self.assertTrue(ra.is_generated_workflow(
            {"path": "dynamic/pages/pages-build-deployment", "name": "pages"}))

    def test_pages_name_is_generated(self):
        self.assertTrue(ra.is_generated_workflow(
            {"path": ".github/workflows/pages-build-deployment", "name": "pages"}))

    def test_empty_is_not_generated(self):
        self.assertFalse(ra.is_generated_workflow({}))
        self.assertFalse(ra.is_generated_workflow(None))


class TestApiSafeLen(unittest.TestCase):
    def test_missing_size_is_zero(self):
        self.assertEqual(ra.api_safe_len({}), 0)
        self.assertEqual(ra.api_safe_len(None), 0)

    def test_size_is_int(self):
        self.assertEqual(ra.api_safe_len({"size": "42"}), 42)


class TestCiRunsTests(unittest.TestCase):
    def test_detects_pytest(self):
        data = {"workflow_files": {"ci.yml": "steps:\n  - run: pytest -q\n"}}
        self.assertTrue(ra._ci_runs_tests(data))

    def test_detects_npm_test(self):
        data = {"workflow_files": {"ci.yml": "run: npm test\n"}}
        self.assertTrue(ra._ci_runs_tests(data))

    def test_no_workflows_is_false(self):
        self.assertFalse(ra._ci_runs_tests({}))

    def test_lint_only_workflow_is_false(self):
        data = {"workflow_files": {"ci.yml": "run: ruff check .\n"}}
        self.assertFalse(ra._ci_runs_tests(data))


class TestWorkflowTriggers(unittest.TestCase):
    def test_inline_on(self):
        self.assertIn("push", ra.workflow_triggers(
            {"workflow_files": {"a.yml": "on: push\n"}}))

    def test_mapping_on_block(self):
        text = "on:\n  pull_request:\n  push:\n"
        got = ra.workflow_triggers({"workflow_files": {"a.yml": text}})
        self.assertEqual(got, {"pull_request", "push"})

    def test_bracket_list(self):
        self.assertIn("push", ra.workflow_triggers(
            {"workflow_files": {"a.yml": "on: [push, pull_request]\n"}}))

    def test_no_on_is_empty(self):
        self.assertEqual(ra.workflow_triggers({}), set())


class TestBand(unittest.TestCase):
    def test_bands(self):
        for score, want in [(100, "Strong"), (85, "Strong"), (70, "Good"),
                            (50, "Needs work"), (49, "At risk"), (0, "At risk")]:
            self.assertEqual(ra.band(score), want, score)


class TestSuggestedLabels(unittest.TestCase):
    def test_high_severity_adds_bug(self):
        labels = ra.suggested_labels(finding("x", "high", "Testing"))
        self.assertIn("testing", labels)
        self.assertIn("bug", labels)

    def test_low_severity_omits_bug(self):
        labels = ra.suggested_labels(finding("x", "low", "CI/CD"))
        self.assertIn("ci", labels)
        self.assertNotIn("bug", labels)

    def test_labels_are_deduplicated(self):
        labels = ra.suggested_labels(finding("x", "high", "Documentation"))
        self.assertEqual(len(labels.split(", ")), len(set(labels.split(", "))))


class TestTeaserBoundary(unittest.TestCase):
    """The free scan must not give away the paid report."""

    def setUp(self):
        self.findings = [finding("F%d" % i) for i in range(1, 8)]
        self.total = 39

    def teaser(self):
        return ra.render_report("o/r", repo_data(), dimensions(), self.findings,
                                self.total, "now", teaser=True)

    def full(self):
        return ra.render_report("o/r", repo_data(), dimensions(), self.findings,
                                self.total, "now", teaser=False)

    def test_teaser_has_headline_and_dimensions(self):
        t = self.teaser()
        self.assertIn("Free repo health scan", t)
        self.assertIn("**Overall score: 39 / 100", t)
        self.assertIn("## Dimension scores", t)

    def test_teaser_shows_only_top_three_titles(self):
        t = self.teaser()
        self.assertIn("F1", t)
        self.assertIn("F3", t)
        self.assertNotIn("F4", t)

    def test_teaser_has_upgrade_cta(self):
        t = self.teaser()
        self.assertIn("Want the full audit", t)
        self.assertIn("£29", t)

    def test_teaser_leaks_no_written_findings(self):
        t = self.teaser()
        self.assertNotIn("### 1.", t)
        self.assertNotIn("Fix it.", t)

    def test_teaser_leaks_no_per_check_detail(self):
        self.assertNotIn("## Dimension detail", self.teaser())

    def test_full_report_has_written_findings_and_detail(self):
        f = self.full()
        self.assertIn("Repository health audit", f)
        self.assertIn("### 1.", f)
        self.assertIn("Fix it.", f)
        self.assertIn("## Dimension detail", f)
        self.assertNotIn("Want the full audit", f)

    def test_no_findings_still_renders(self):
        t = ra.render_report("o/r", repo_data(), dimensions(), [], 100, "now",
                             teaser=True)
        self.assertIn("No gaps found", t)


class TestRenderIssues(unittest.TestCase):
    def test_one_entry_per_finding_in_severity_order(self):
        findings = [finding("Low one", "low"), finding("High one", "high")]
        out = ra.render_issues("o/r", findings)
        self.assertLess(out.index("High one"), out.index("Low one"))
        self.assertIn("- Suggested labels:", out)

    def test_body_is_fenced(self):
        out = ra.render_issues("o/r", [finding("x")])
        self.assertIn("```\nFix it.\n```", out)


class TestRequestResolver(unittest.TestCase):
    def test_url(self):
        self.assertEqual(rar.candidate_from("https://github.com/cesanta/mongoose"),
                         "cesanta/mongoose")

    def test_url_with_trailing_slash_and_dot_git(self):
        self.assertEqual(rar.candidate_from("see https://github.com/foo/bar.git now"),
                         "foo/bar")

    def test_bare_slug(self):
        self.assertEqual(rar.candidate_from("owner/repo"), "owner/repo")

    def test_no_repo(self):
        self.assertIsNone(rar.candidate_from("no repository here"))
        self.assertIsNone(rar.candidate_from(""))

    def test_trailing_punctuation(self):
        self.assertEqual(rar.candidate_from("https://github.com/a/b)"), "a/b")

    def test_resolve_prefers_dispatch(self):
        import os
        old = os.environ.copy()
        try:
            os.environ["DISPATCH_REPO"] = "from/dispatch"
            os.environ["ISSUE_BODY"] = "https://github.com/from/issue"
            self.assertEqual(rar.resolve_repo(), "from/dispatch")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
