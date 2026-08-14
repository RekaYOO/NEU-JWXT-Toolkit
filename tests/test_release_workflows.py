from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(
    encoding="utf-8"
)


def test_ci_avoids_duplicate_push_and_pull_request_runs():
    assert 'branches: ["main"]' in CI
    assert "pull_request:" in CI
    assert 'branches: ["**"]' not in CI


def test_ci_owns_the_complete_source_quality_gate():
    expected_commands = (
        "python -m pytest backend/tests",
        "python -m compileall -q backend launchers",
        "npm test",
        "npm run build",
        "python -m pytest tests",
    )
    assert all(command in CI for command in expected_commands)


def test_release_builds_web_once_without_repeating_source_tests():
    duplicated_commands = (
        "python -m pytest backend/tests",
        "python -m compileall -q backend launchers",
        "npm test",
        "python -m pytest tests",
    )
    assert all(command not in RELEASE for command in duplicated_commands)
    assert RELEASE.count("npm run build") == 1
    assert RELEASE.count("name: web-build") == 3
    assert "run-id:" not in RELEASE
    assert "github-token:" not in RELEASE


def test_release_dag_stays_small_and_explicit():
    assert RELEASE.count("needs: [validate, web]") == 2
    assert "needs: [validate, windows, linux]" in RELEASE
    assert "ci_gate:" not in RELEASE


def test_release_only_publishes_version_tags_and_final_artifacts():
    assert 'tags: ["v*"]' in RELEASE
    assert "workflow_dispatch:" in RELEASE
    assert "if: startsWith(github.ref, 'refs/tags/v')" in RELEASE
    assert 'pattern: "*-release"' in RELEASE


def test_windows_release_keeps_compiled_portable_but_drops_unsigned_installer():
    assert "python packaging/nuitka/build.py desktop" in RELEASE
    assert 'python-version: "3.11"' in RELEASE
    assert "Compile standalone desktop application with Nuitka" in RELEASE
    assert "Build Inno Setup installer" not in RELEASE
    assert "windows-x64-setup.exe" not in RELEASE
    assert "VersionInfo" in RELEASE
    assert "NEU-JWXT-Toolkit Contributors" in RELEASE
    assert "Portable archive contents differ from the validated standalone payload" in RELEASE


def test_windows_release_does_not_use_antivirus_as_a_nondeterministic_gate():
    assert "Scan final Windows artifacts with Microsoft Defender" not in RELEASE
    assert "MpCmdRun" not in RELEASE
    assert "-SignatureUpdate" not in RELEASE
    assert "Automated antivirus scanning is not used as a release gate" in RELEASE
    assert "SHA256SUMS.txt" in RELEASE
    assert "actions/attest@" in RELEASE
