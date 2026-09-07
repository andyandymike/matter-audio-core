# GitHub governance

Repository settings and rulesets are managed through the GitHub REST API. Keep
authentication material out of the repository and inspect current state before
updating an existing rule.

## Main branch rules

Two independent rulesets target the default branch, `main`:

| Ruleset | Policy | Administrator bypass |
| --- | --- | --- |
| **Main branch safety** | No branch deletion, no force pushes, linear history. | None. Applies to everyone. |
| **Main contribution policy** | Pull request, one code-owner approval, current review approval, resolved conversations, `Release Gate` and `Docs build`. | `always`, for repository administrators. |

The repository owner, `@andyandymike`, can make an intentional ordinary direct
push to `main`. The separate safety rules still apply, so that permission does
not permit force pushes, branch deletion or merge commits. Contributors use
squash or rebase merges after passing the required checks. Required checks are
bound to the GitHub Actions application and require an up-to-date branch.

An administrator's direct push still triggers the full test and documentation
workflows. Inspect their results before considering the change ready for use.

## CI and documentation deployment

- `Tests` builds and installs the distribution, checks package contents, runs
  unit tests and the complete CLI examples on Windows/Linux with Python
  3.10, 3.12 and 3.14. A stable **Release Gate** aggregates all six jobs.
- `Docs build` runs on every pull request and push to `main`. There is no path
  filter that could leave a required check missing.
- `Docs deploy` publishes only successful builds from `main` to the
  `github-pages` environment. That environment permits the `main` branch only.
- Pages uses a custom GitHub Actions workflow with OpenID Connect. Only the
  deployment job receives `pages: write` and `id-token: write`.
- Workflow tokens default to read-only access and cannot approve pull requests.
  Workflow action references are pinned to full commit SHAs. The allowed Actions
  policy permits GitHub-owned actions; add any third-party action explicitly
  after reviewing it.
- Dependabot checks Python and GitHub Actions dependencies weekly. Security
  alerts and automated security-fix pull requests are enabled. Secret scanning
  and push protection remain enabled.

The site builds with pinned MkDocs Material. Public documentation and its assets
live in `docs/`; the generated site stays under ignored `.local/docs-site/`.
No recordings, model files or private workspaces are published by this workflow.

## Inspect and maintain

```sh
gh api repos/andyandymike/matter-audio-core/rulesets
gh api repos/andyandymike/matter-audio-core/pages
gh api repos/andyandymike/matter-audio-core/actions/permissions
gh api repos/andyandymike/matter-audio-core/actions/permissions/workflow
```

Fetch each ruleset's full definition before updating it. Preserve the
administrator `always` bypass on the contribution policy and the empty bypass
list on the safety policy. Add a new required check only when its exact job name
is present and its workflow reliably runs for contributions.

API references: [repository rulesets](https://docs.github.com/en/rest/repos/rules),
[GitHub Pages](https://docs.github.com/en/rest/pages/pages), and
[Actions permissions](https://docs.github.com/en/rest/actions/permissions).
