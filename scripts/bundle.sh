#!/usr/bin/env bash

base_dir="$(pwd)"
dist_dir="$base_dir/dist"
plugin_name="hardcover-sync"
package_name="hardcover_sync"

if [ ! -d "$dist_dir" ]; then
	mkdir -p "$dist_dir"
fi

build_dir="$(mktemp -d)"
echo "Created build directory $build_dir"
trap "rm -r '$build_dir' || true" EXIT

workspace_deps=($(uv export --package "$plugin_name" --no-header --no-annotate --format requirements.txt --no-emit-project --no-hashes | grep '-e ./' | cut -d ' ' -f 2))

uv pip install "$base_dir" --target "$build_dir" --link-mode copy

for dep in ${workspace_deps[@]}; do
	uv pip install "$dep" --target "$build_dir" --link-mode copy
done

mv "$build_dir/$package_name/"* "$build_dir"

version="$(grep -h -e '^Version:' "$build_dir/$package_name"-*.dist-info/METADATA 2>/dev/null | head -1 | cut -d ' ' -f 2)"
if [ -z "$version" ]; then
	echo "Could not determine version for $plugin_name"
	exit 1
fi

find "$build_dir" -depth \( -name '*.dist-info' -o -name '.lock' -o -name '__pycache__' \) -exec rm -r "{}" \;
find "$build_dir" -type d -empty -delete
touch "$build_dir/plugin-import-name-$package_name.txt"

out_zip="$dist_dir/$plugin_name-$version.zip"
(
	cd "$build_dir"
	zip -rq "$out_zip" .
)
echo "Bundled $plugin_name plugin to $out_zip"
