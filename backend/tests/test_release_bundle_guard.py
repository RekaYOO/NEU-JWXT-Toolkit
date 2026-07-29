from pathlib import Path

from tools.check_release_bundle import find_forbidden


def test_release_bundle_guard_rejects_runtime_and_personal_data(tmp_path):
    root = tmp_path / "bundle"
    (root / "app").mkdir(parents=True)
    (root / "app" / "cache.db-wal").write_bytes(b"private")
    (root / "grade_tracking_outbox.json").write_text(
        "{}", encoding="utf-8"
    )
    (root / "data").mkdir()
    (root / "data" / "session.json").write_text("{}", encoding="utf-8")

    found = {str(path).replace("\\", "/") for path in find_forbidden(root)}

    assert "app/cache.db-wal" in found
    assert "grade_tracking_outbox.json" in found
    assert "data" in found
    assert "data/session.json" in found


def test_release_bundle_guard_allows_program_and_examples(tmp_path):
    root = tmp_path / "bundle"
    (root / "app").mkdir(parents=True)
    (root / "examples").mkdir()
    (root / "app" / "neu-jwxt-server").write_bytes(b"binary")
    (root / "examples" / "Caddyfile").write_text(
        "reverse_proxy 127.0.0.1:8000", encoding="utf-8"
    )
    (root / "examples" / "config.example.json").write_text(
        "{}", encoding="utf-8"
    )

    assert find_forbidden(root) == []
