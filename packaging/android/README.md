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

`ANDROID_API_LEVEL=24` is the actual cross-build variable. The similarly named
`CIBW_ANDROID_API_LEVEL` is not supported by this pinned cibuildwheel version.
The wheel preparation and recipe unit tests have passed on Windows; complete Linux
cross compilation, ABI loading and 16 KiB page-size device checks remain unverified.

## Acceptance

CI gates release on instrumented React rendering, protected FastAPI health and
imports of the three native dependencies. It does not yet cover every workflow
from the requested acceptance plan. HTTPS proxy/login, all document-picker paths,
notification deep links and permissions, task lifecycle, boot recovery and
arm64 data-preserving upgrades still need end-to-end/device acceptance.

Never use `-x install...PythonRequirements` in CI or release builds. It is useful
only for isolated Java diagnostics on a host without the Android wheelhouse.
