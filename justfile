set windows-shell := ["pwsh.exe", "-c"]

# Print the help message.
@help:
    echo "Usage: just [RECIPE]\n"
    just --list

# Run all pre-commit hooks.
pre-commit:
    uv run prek run --all-files

# Aliases
alias pc := pre-commit
