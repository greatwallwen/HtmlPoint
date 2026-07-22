#!/bin/sh

set -eu

if [ "$#" -ne 0 ]; then
    echo "usage: platform/start-course-studio.sh" >&2
    exit 64
fi

platform_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(dirname -- "$platform_root")
web_root="$platform_root/web"
helper_root="$platform_root/helper"
dist="$web_root/dist"
python_bin="$repo_root/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
    echo "course-studio: Python environment not found at $python_bin" >&2
    echo "Create it from the repository root:" >&2
    echo "  python3 -m venv .venv" >&2
    echo "  .venv/bin/python -m pip install -e \"platform/helper[dev]\"" >&2
    exit 1
fi

if ! "$python_bin" -c \
    'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
then
    python_version=$("$python_bin" --version 2>&1 || true)
    echo "course-studio: $python_bin must use Python 3.12; found: ${python_version:-unknown}" >&2
    echo "Recreate it from the repository root:" >&2
    echo "  python3.12 -m venv .venv" >&2
    echo "  .venv/bin/python -m pip install -e \"platform/helper[dev]\"" >&2
    exit 1
fi

case "$(uname -s)" in
    Darwin)
        app_data="$HOME/Library/Application Support/CourseStudio"
        ;;
    Linux)
        app_data="${XDG_DATA_HOME:-$HOME/.local/share}/CourseStudio"
        ;;
    *)
        echo "course-studio: this launcher supports macOS and Linux only" >&2
        exit 1
        ;;
esac

source_root="$app_data/sources"
database="$app_data/knowledge.db"

if ! mkdir -p "$app_data" "$source_root"; then
    echo "course-studio: could not create the data directory: $app_data" >&2
    exit 1
fi

if [ ! -f "$dist/.vite/manifest.json" ]; then
    if ! command -v npm >/dev/null 2>&1; then
        echo "course-studio: npm was not found on PATH" >&2
        echo "Install a Node.js distribution that includes npm, then run:" >&2
        echo "  npm --prefix platform/web ci" >&2
        exit 1
    fi
    if [ ! -x "$web_root/node_modules/.bin/vite" ]; then
        echo "course-studio: Web dependencies are not installed" >&2
        echo "Run this from the repository root:" >&2
        echo "  npm --prefix platform/web ci" >&2
        exit 1
    fi
    if ! npm --prefix "$web_root" run build; then
        echo "course-studio: Web build failed" >&2
        echo "Fix the build error above, then run this launcher again." >&2
        exit 1
    fi
fi

cd "$helper_root"
exec "$python_bin" -m course_helper \
    --database "$database" \
    --app-data "$app_data" \
    --reference-root "$source_root" \
    --web-origin "http://127.0.0.1:8765" \
    --web-root "$dist" \
    --port 8765
