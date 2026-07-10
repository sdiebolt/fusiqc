# fUSIQC Agent Guidelines

## Project Status

This is a **beta package** under rapid iteration. Backward compatibility is not a
concern; breaking changes are acceptable when they simplify the design.

## Release Process

Releases are published by GitHub Actions via `.github/workflows/publish.yml`.

- Bump `version` in `pyproject.toml`.
- Commit using the commit convention below.
- Create a `v*` tag (for example `v0.1.0`).
- Push the branch and the tag.

Pushing a `v*` tag triggers lint, build, and `uv publish` to PyPI.

## Build, Lint, and Test Commands

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and
[just](https://github.com/casey/just) as a tiny command runner.

### Build and Environment
- `uv sync` - Install dependencies and sync the virtual environment.
- `uv build` - Build the package.

### Linting and Formatting
- `just pre-commit` (or `just pc`) - Run all pre-commit hooks.
- `uv run ruff check src/ --fix` - Run Ruff linter with auto-fix.
- `uv run ruff format src/` - Format code with Ruff.
- `uv run ty check src/` - Run type checking.
- `uv run codespell` - Run spell checking.

### Testing
There is currently no dedicated test suite. For non-trivial logic, leave behind the
smallest useful check instead of building a large harness.

## Code Style Guidelines

### Imports
- Use absolute imports.
- Group imports: standard library, third-party, local modules.

### Formatting
- Use Ruff for formatting.
- Use double quotes for strings unless escaping makes single quotes clearer.

### Comments
1. Comments should not duplicate code.
2. Explain unidiomatic code and non-obvious behavior.
3. Use `TODO:` for incomplete work.
4. End comments with a period.

### Types
- Use type hints throughout.
- Prefer standard library types like `list[...]`, `dict[...]`, and `tuple[...]`.
- Use `TypedDict` only when a plain dict is no longer clear enough.

### Naming
- Functions and methods: `snake_case`.
- Classes: `PascalCase`.
- Constants: `UPPER_CASE`.
- Private helpers: leading underscore.

### Error Handling
- Raise specific exceptions.
- Validate inputs early with clear error messages.
- Use warnings only for non-fatal situations.

### Documentation
- Keep docstrings concise and useful.
- Prefer documenting behavior and edge cases over repeating type hints.
- Use single backticks for inline code.

### Code Structure
- Use `pathlib.Path` for filesystem work.
- Use context managers for files and other resources.
- Reuse existing helpers before adding new abstractions.
- Keep functions focused and small.

## Commit Message Convention

This project follows a Commitizen-style convention:

```text
<type>(<scope>): <short summary>
```

Scope is optional when it adds no value, but prefer using one when a component is clear.

### Types
- `feat`: new feature.
- `fix`: bug fix.
- `docs`: documentation only.
- `style`: formatting or style-only change.
- `refactor`: structural change without behavior change.
- `perf`: performance improvement.
- `test`: tests.
- `chore`: build, release, or tooling.

### Suggested Scopes
- `cli` - command-line interface.
- `qc` - QC table generation and plots.
- `web` - local review web app.
- `dataset` - dataset discovery and indexing.
- `config` - configuration handling.
- `release` - packaging and publishing.

Examples:
- `feat(cli): add --refresh flag`
- `fix(qc): show progress while scanning dataset`
- `chore: release 0.1.0`

## Practical Rules

- Prefer the smallest working change.
- Do not add abstractions for hypothetical future needs.
- Reuse stdlib and existing dependencies before adding new ones.
- Keep the diff small, but read the full flow before editing.
