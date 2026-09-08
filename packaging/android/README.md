# Android Build

This integration is not release-ready until both native wheel builds, emulator
checks and arm64 device acceptance pass. A successful Java-only local-app compile
does not create a runnable local APK.

## Layout

- `shared`: WebView, origin confinement, Axios bridge and document picker.
- `client-app`: HTTPS configuration and encrypted, origin-scoped cookies.
- `local-app`: Chaquopy/FastAPI process lifecycle, foreground tasks and notification outbox.
- `recipes`: verified upstream source downloads and Android PEP 517 build hooks.

The client never depends on Chaquopy. `VERSION` and `ANDROID_VERSION_CODE` are read
from the repository root. Do not change signing keys between releases.

## Shared Runtime Contracts

Both APKs use the same frontend API, authentication flows and page-loading rules
as the web app. The local APK uses the same Python storage/cache coordinator and
academic networking code, with private data paths and native notifications.
Course outlines remain no-store and load on page entry.

The native transport applies the Axios deadline to connect/read/write and the
whole call. Inheriting OkHttp's shorter socket timeout discards legitimate slow
backend responses, including direct-login WebVPN hints. Connection recovery is
allowed before a request is sent; one-shot bodies prohibit replay after dispatch
or HTTP 408/503 follow-ups. Local loopback traffic explicitly bypasses system HTTP
proxies; academic traffic still follows the shared Python direct/WebVPN policy.

One-shot requests also use a fresh, non-retaining connection pool for each call.
An idle pooled socket can close after its health check but before the request body
is sent; replaying that request is unsafe and previously surfaced as a first-click
network failure. GET/HEAD retain pooling, while new mutation connections retain
the same TLS validation, cookie jar, proxy policy and fixed native headers.
Each private pool is evicted after the response is fully consumed and closed,
not via zero-idle eviction that can race HTTP/2 body reads. Delivery occurs once,
after response cleanup. The HTTPS regression exercises 100 consecutive fresh
requests with cookies and fixed native headers.

## Build Prerequisites

Use Linux for native wheels: Python 3.13, Node 20, JDK 17, Android SDK 35/build-tools
35.0.0, NDK 27.2.12479018, Rust with the matching Android target, GCC/make,
pkg-config and patchelf. `cibuildwheel==3.1.4` supplies the Android CPython cross
environment; its pinned CPython artifact selects this NDK version.

Build the frontend with `npm ci && npm run build` in `frontend/`, then:

```sh
bash packaging/android/build_android_wheels.sh x86_64
cd packaging/android
bash gradlew :shared:testDebugUnitTest testX86TestDebugUnitTest \
  :client-app:assembleX86TestDebug :local-app:assembleX86TestDebug
bash gradlew :client-app:connectedX86TestDebugAndroidTest \
  :local-app:connectedX86TestDebugAndroidTest
```

Use `arm64_v8a` for the release wheel build and `assembleArm64Release` for the APKs.
The release signing environment is `ANDROID_KEYSTORE_FILE` plus the three password/
alias variables documented in the release workflow. GitHub decodes the keystore
from `ANDROID_SIGNING_KEYSTORE_BASE64` into a private temporary file and removes it
after the job. Debug builds use the Android debug key only.

## Native Dependencies

Versions exactly match `requirements.lock`; no Termux wheel or dependency downgrade
is used. Source archives in `recipes/sources.json` are checked against pinned
SHA-256 digests before extraction into a fresh temporary directory.

- `pydantic-core`: target Rust triplet, NDK linker and Android CPython library
  directory are passed to PyO3/maturin inside the cross environment.
- `lxml`: static libxml2 2.15.2, libxslt 1.1.45, libiconv 1.19 and zlib 1.3.2;
  archive hashes are provided by the locked lxml source. Autoconf receives explicit
  host/build triplets; zlib uses its own static build configuration.
- `pycryptodome`: its upstream compile/link feature probes use the NDK compiler
  and target sysconfig, without executing Android binaries on the build host.
- Pure dependencies are downloaded with `--platform any` so host-native wheels
  cannot accidentally enter the wheelhouse.
- Android's Uvicorn wheel defers CLI imports, avoiding `_multiprocessing` during
  single-process `Config`/`Server` startup. Its version, server implementation and
  license are unchanged; the wheel RECORD is regenerated and the adaptation is
  identified by `uvicorn/ANDROID_COMPATIBILITY.txt`. Desktop/server wheels are untouched.
- Chaquopy extracts `Crypto` into private storage because PyCryptodome loads
  ctypes libraries by filesystem path instead of Python's APK importer.

`ANDROID_API_LEVEL=24` is the actual cross-build variable. The similarly named
`CIBW_ANDROID_API_LEVEL` is not supported by this pinned cibuildwheel version.
Linux cross compilation now passes for both arm64 and x86_64 with the locked native
versions. Packaged ELF architecture and 16 KiB alignment are checked. Runtime module
loading, Android startup and real-device page-size acceptance remain separate gates.

## Acceptance

The branch CI runs source quality checks and the two native ABI builds in parallel.
Only complete wheelhouses are cached, keyed by the locked dependencies and recipes.
Debug APK jobs reuse the tested frontend and wheel artifacts. The Release workflow
reuses the same arm64 wheel artifact instead of compiling it again after validation.

CI gates release on instrumented React rendering, protected FastAPI health,
imports and AES execution, service recreation, HTTPS bridge/cookie isolation,
system document-picker save/cancel, notification delivery/acknowledgement/deep links,
and foreground service shutdown after tasks stop. A separate instrumentation
process tests enabling/stopping tasks after POST_NOTIFICATIONS is revoked.
The full gate passed in Actions run 34223603664 (build commit d97a836).
Local regression passed 811 Python tests (2 skipped for Windows symlink limits)
and 337 frontend tests across 39 suites; production build and size budgets passed.
Local-screen pixel checks supplement the DOM and compositor assertions; both
application screenshots were also visually inspected.

Parity regression coverage includes delayed direct-login hints, a single SMS
verification, automatic first-page outline reads, four runtime profiles across
process restarts, and an instrumentation-only Python fixture for 11-second real
loopback responses and Android SQLite/credential persistence. These checks passed
in the same full CI run. The fixture stubs
upstream academic operations, not the bridge or FastAPI routes, and is excluded
from the application APK. This does not replace real-account device acceptance.

Real academic login, long-running background work, boot recovery, every export
format, API 24 device behavior, arm64 data-preserving upgrades and the fixed-key
minified release still need device/release acceptance. Debug builds use separate
package names and ephemeral Runner keys, not the persistent release signing key.

Never use `-x install...PythonRequirements` in CI or release builds. It is useful
only for isolated Java diagnostics on a host without the Android wheelhouse.
