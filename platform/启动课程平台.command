#!/bin/sh

set -eu

if [ "$#" -ne 0 ]; then
    echo "usage: platform/启动课程平台.command" >&2
    exit 64
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$script_dir/start-course-studio.sh"
