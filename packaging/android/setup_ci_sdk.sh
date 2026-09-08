#!/usr/bin/env bash
set -euo pipefail

sdk_bin="${ANDROID_HOME:?ANDROID_HOME is required}/cmdline-tools/latest/bin"
test -x "$sdk_bin/sdkmanager"
export PATH="$sdk_bin:$ANDROID_HOME/platform-tools:$PATH"
if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$sdk_bin" "$ANDROID_HOME/platform-tools" >> "$GITHUB_PATH"
fi
set +o pipefail
yes | sdkmanager --licenses >/dev/null
set -o pipefail
sdkmanager "$@"
