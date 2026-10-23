# Contributing

Thanks for helping out. This is an unofficial integration, it isn't made, supported or endorsed by Tel2.

Bugs and feature requests go in the issue tracker, there's a form for each. For anything bigger than a small fix, open an issue first so we can talk it through.

## Getting set up

You need [mise](https://mise.jdx.dev). It installs the Python and uv versions pinned in `mise.toml`.

```sh
mise trust
mise install
uv venv --python 3.14 .venv
uv pip install -r requirements_test.txt
```

If mise isn't activated in your shell, prefix the uv commands with `mise exec --`.

Everything after that runs from `.venv`, either as `.venv/bin/...` or after `source .venv/bin/activate`.

## Running the checks

CI runs these on every push and PR, run them before you push:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov=custom_components.tel2 --cov-report=term-missing -q
```

The tests run offline, they never talk to Tel2.

pre-commit is optional. It runs ruff and a few file checks on each commit:

```sh
uvx pre-commit install
```

## Fixtures

Tests use the synthetic fixtures in `tests/fixtures`, built from the documented API shapes. `tests/fixtures/index.json` lists every file and marks its source.

If a test needs a response that isn't there, add a synthetic fixture and an index entry for it.

## Capturing fixtures

`scripts/capture_fixtures.py` records real Tel2 responses so we can check the synthetic ones against them. Run it from the dev environment set up by mise and uv above, it imports the integration and Home Assistant:

```sh
.venv/bin/python scripts/capture_fixtures.py
```

- It prompts for a Tel2 portal login and password, use a dedicated login rather than your own.
- To skip the prompts, set `TEL2_LOGIN` and `TEL2_PASSWORD` in the environment. Keep them in a git-ignored file such as `.env`, never in the repo.
- It only calls read-only actions, nothing on the account is changed.
- Every response goes through `scripts/anonymise.py` before anything is written.
- If anything still looks personal after that, it writes nothing and lists the JSON paths that failed.
- Files go to `tests/fixtures/captured` with their own `index.json`, `--out` changes that.

## Reviewing captures before committing

This repo is public, so someone has to read every captured file before it's committed. The anonymiser is a safety net, not a sign-off.

- Open each file and read the whole thing, don't just skim the diff.
- Look for real phone numbers, names, emails, addresses, account codes, IPs, tokens, passwords and PINs.
- Placeholders look like `01632 960xxx` numbers (Ofcom's drama range), `Example Customer`, `ZZ99 9ZZ`, `00000000` and `[REDACTED]`.
- If something got through, delete the capture, extend the rule in `scripts/anonymise.py` with a test, and capture again.
- If in doubt, leave it out.

## Pull requests

Keep PRs small, one change each, and fill in the template.

The maintainer labels each PR, the release notes are grouped by label:

- `breaking` for changes users have to act on.
- `feature` for new behaviour.
- `fix` for bug fixes.
- `docs` for the README and other docs.
- `maintenance` for tooling, CI and dependency bumps.

## Releases

Releases are done by the maintainer, please don't bump the version in a PR.

- Bump `version` in `custom_components/tel2/manifest.json`.
- Tag that commit `vX.Y.Z`, matching the manifest version.
- Run the Release workflow by hand with the tag first and check it passes, a mismatch after publishing leaves a release without `tel2.zip`.
- Publish a GitHub release from the tag, the notes are generated from the PR labels. Tick Set as a pre-release for `bN` versions.
- The release workflow checks the tag matches the manifest and attaches `tel2.zip`, which is what HACS installs.
