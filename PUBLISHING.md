# Publishing `echo-desktop`

The name is free on PyPI (checked: `https://pypi.org/pypi/echo-desktop/json` →
404), and `agent-desktop-py` is free too.

## Fastest path: the release workflow (recommended)

No API token is stored anywhere. Push a tag and GitHub Actions builds, tests and
publishes over OIDC (Trusted Publishing).

**One-time setup**

1. Create the project on PyPI as a *pending publisher* if you have never
   published it: PyPI → *Your projects* → *Publishing* → **Add a new pending
   publisher**, with:
   - PyPI project name: `echo-desktop`
   - Owner: `gitofmark`
   - Repository: `agent-desktop`
   - Workflow name: `release.yml`
   - Environment: `pypi`
2. In the GitHub repo: Settings → Environments → new environment named `pypi`
   (no secrets needed; you may add required reviewers here).

**Release**

```bash
git tag v1.0.0
git push origin v1.0.0          # builds, tests, publishes, attaches dist/
```

Running the workflow manually (`workflow_dispatch`) builds and tests but does
**not** upload — useful for a dry run.

## Manual path (if you would rather not use Actions)

```bash
python -m pip install --upgrade build twine
python -m build                 # dist/echo_desktop-1.0.0.tar.gz + .whl
python -m twine check dist/*
python -m twine upload dist/*   # prompts for the API token
```

Use a **project-scoped** API token (PyPI → *Account settings* → *API tokens* →
scope `echo-desktop`), not your account password. Test on TestPyPI first if you
like:

```bash
python -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple --extra-index-url https://pypi.org/simple echo-desktop
```

## What is in the box

| Artifact | Contents |
| --- | --- |
| wheel (`py3-none-any`, ~74 KB) | `snapshot_parser/` (20 modules) + `LICENSE` + entry points |
| sdist (~96 KB) | the above, plus `pyproject.toml`, `README.md` |
| entry points | `echo-desktop`, `agent-desktop-py` |

Verified locally before writing this:

```
twine check dist/*                PASSED (both artifacts)
clean-venv install                echo-desktop 1.0.0
run with no repo on disk          full snapshot, correct output
registry probes                   echo-desktop free on PyPI and npm
```

## After you publish: does it work on Windows without pywinauto?

The core has **zero runtime dependencies**. The snapshot parser, the fixture
backend, the geometry maths and the CLI all work on a bare
`pip install echo-desktop`. Only *live* reading of window trees needs the
Windows extras:

```bash
pip install "echo-desktop[windows]"    # pywinauto + uiautomation
```

So `echo-desktop doctor` is meaningful immediately: it tells you whether a
backend is usable and what to install if not.

## Versioning

`version` lives in two places and must agree — `pyproject.toml` and
`snapshot_parser/__init__.py`. Bump both, then tag the same number. Remove the
`agent-desktop-py` alias and this file's alias section in **1.1**, as the
deprecation notice promises.
