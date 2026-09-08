#!/usr/bin/env bash
set -euo pipefail

abi="${1:?usage: build_android_wheels.sh <arm64_v8a|x86_64>}"
case "$abi" in
  arm64_v8a|x86_64) ;;
  *) echo "Unsupported Android ABI: $abi" >&2; exit 2 ;;
esac

root="$(cd "$(dirname "$0")/../.." && pwd)"
android_root="$root/packaging/android"
wheelhouse="$android_root/wheelhouse"
work="$(mktemp -d "${RUNNER_TEMP:-/tmp}/neu-android-${abi}.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/sdists" "$work/src" "$wheelhouse"

python -m pip install --disable-pip-version-check "cibuildwheel==3.1.4" "tomlkit==0.13.3"
case "$abi" in
  arm64_v8a) rust_target=aarch64-linux-android ;;
  x86_64) rust_target=x86_64-linux-android ;;
esac
rustup target add "$rust_target"

python "$android_root/recipes/prepare.py" "$work/sdists" "$work/src"

export CIBW_BUILD="cp313-*"
export CIBW_ARCHS_ANDROID="$abi"
export CIBW_BUILD_VERBOSITY=1
export ANDROID_API_LEVEL=24
export CIBW_ENVIRONMENT_ANDROID='ANDROID_API_LEVEL=24'

for source in "$work"/src/*; do
  python -m cibuildwheel --platform android --output-dir "$wheelhouse" "$source"
done

grep -Ev '^(pydantic_core|lxml|pycryptodome)==' "$android_root/requirements-android.txt" \
  > "$work/pure-requirements.txt"
python -m pip download --disable-pip-version-check --no-deps --only-binary=:all: \
  --platform any --implementation py --abi none --python-version 3.13 \
  --dest "$wheelhouse" -r "$work/pure-requirements.txt"

for package in pydantic_core lxml pycryptodome; do
  if ! find "$wheelhouse" -maxdepth 1 -type f -iname "${package//-/_}-*android_24_${abi}.whl" | grep -q .; then
    echo "Missing Android wheel for $package" >&2
    exit 1
  fi
done
