# Ready-to-file issue checklist — `agentbarto90-eng/agent-landing-page`

One entry per finding. Copy the title and body into a new issue, or use `gh issue create`.

## 1. Add a README with install, usage and contribution basics

- Severity: High
- Area: Documentation
- Suggested labels: `documentation, bug, good first issue`

```
There is no README at the repository root. A first-time visitor cannot tell what the project does or how to run it.

Suggested README outline:
1. What this project does (one paragraph)
2. Who it is for
3. Install / quick start
4. Usage examples
5. How to run tests
6. How to contribute and where to ask questions
7. License
```

## 2. Add a license so the project is legally usable

- Severity: High
- Area: Documentation
- Suggested labels: `documentation, bug, good first issue`

```
No license file was detected. Without a license, the default is all rights reserved: companies and many contributors cannot legally use or contribute to the code. Pick one at https://choosealicense.com/ and add it as LICENSE.
```

## 3. Add a minimal automated test suite and a way to run it

- Severity: High
- Area: Testing
- Suggested labels: `testing, bug, good first issue`

```
No tests were detected. That makes every future change a gamble and makes reviewers verify behaviour by hand. Start with one happy-path test for the public interface and a documented command (for example `make test`) so CI and contributors run the same thing.
```

## 4. Run the test suite in CI on every push and pull request

- Severity: High
- Area: Testing
- Suggested labels: `testing, bug, good first issue`

```
Nothing in CI runs the tests, so regressions can merge unnoticed. Add a workflow triggered by `push` and `pull_request` that installs dependencies and runs the test command. Fail the job on non-zero exit so branch protection can depend on it.
```

## 5. Add a CI workflow (lint and test on push and pull request)

- Severity: High
- Area: CI/CD
- Suggested labels: `ci, bug, good first issue`

```
There are no GitHub Actions workflows. Every quality check is manual today. A single `.github/workflows/ci.yml` that runs on `push` and `pull_request`, installs dependencies, lints and runs the tests is the highest-leverage addition, and it is what branch protection needs to block broken merges.
```

## 6. Add a CONTRIBUTING guide (or a contributing section in the README)

- Severity: Medium
- Area: Documentation
- Suggested labels: `documentation, good first issue`

```
There is no contribution guide. New contributors have to guess the branch, test and review conventions, which raises the drop-off rate on first PRs. A short guide is enough: setup, tests, commit style, how to ask for review.
```

## 7. Add a SECURITY.md with a private vulnerability reporting route

- Severity: Medium
- Area: Documentation
- Suggested labels: `documentation, good first issue`

```
There is no security policy. Without one, security reports land in the public issue tracker. Add SECURITY.md naming a contact and, if available, enable GitHub private vulnerability reporting.
```

## 8. Add issue and pull request templates

- Severity: Medium
- Area: Issue triage
- Suggested labels: `triage, good first issue`

```
Missing templates: issue template, PR template. Templates ask for the version, steps to reproduce and expected behaviour up front, which removes a full round-trip on most reports.
```

## 9. Cut a tagged release so users can pin a version

- Severity: Medium
- Area: Maintenance
- Suggested labels: `maintenance, good first issue`

```
There are no tags or releases. Without a version to pin, downstream users track a moving default branch and cannot roll back. Tag the current stable commit and write short release notes.
```

## 10. Set a one-line repository description and topics

- Severity: Low
- Area: Documentation
- Suggested labels: `documentation, good first issue`

```
The repository has no description. The description and topics are what GitHub search and topic listings use to surface the project, so leaving them blank removes free discovery.
```

## 11. Enable automated dependency updates (Dependabot or Renovate)

- Severity: Low
- Area: CI/CD
- Suggested labels: `ci, good first issue`

```
No dependency-update automation was found. Vulnerable or outdated pins then sit until someone notices. Dependabot is free on public repositories and needs only a small `.github/dependabot.yml`.
```

