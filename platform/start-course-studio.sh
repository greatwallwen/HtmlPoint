#!/bin/sh

set -eu

if [ "$#" -ne 0 ]; then
    echo "usage: platform/start-course-studio.sh" >&2
    exit 64
fi

platform_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
web_root="$platform_root/web"
helper_root="$platform_root/helper"
dist="$web_root/dist"

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

mkdir -p "$app_data" "$source_root"

if [ ! -f "$dist/.vite/manifest.json" ]; then
    npm --prefix "$web_root" run build
fi

cd "$helper_root"
exec python -m course_helper \
    --database "$database" \
    --app-data "$app_data" \
    --reference-root "$source_root" \
    --web-origin "http://127.0.0.1:8765" \
    --web-root "$dist" \
    --port 8765
