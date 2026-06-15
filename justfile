set dotenv-load := true

# list available tasks
list:
    @just --list

# Install dependencies and configure pre-commit hooks
install: && setenv
    uv sync
    uv run lefthook install
    just .calibre/source

# Write to .env with your calibre configuration
setenv:
    -calibre-debug scripts/build_env.py

# Run unit tests
test *ARGS:
    LD_LIBRARY_PATH="$CALIBRE_LIBRARY_PATH" uv run pytest {{ARGS}}

# Run linters
lint:
    uv run ruff check --fix
    uv run pymarkdownlnt fix **/*.md

# Remove temporary files/directories
clean:
    -rm -rv dist
    find . -maxdepth 1 -name '.*cache' -type d -exec rm -rv "{}" \;
    find . -depth -name '*pycache*' -o -name '*.egg-info' -o -name 'build' -type d -exec rm -rv "{}" \;

# Build the Calibre plugin zip
build:
    bash scripts/bundle.sh

# Build and install the plugin
install-plugin: build
    find dist -name 'hardcover-sync-*.zip' | xargs calibre-customize --add-plugin

# Launch Calibre in Debug mode
calibre *ARGS:
    just .calibre/run -g {{ARGS}}

# Bump the version, following SemVer
bump:
    bash scripts/bump.sh hardcover-sync
