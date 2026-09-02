import gzip

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.runtime.static import PrecompressedStaticFiles


def test_precompressed_static_files_negotiate_gzip_and_immutable_cache(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    source = b"console.log('performance');" * 200
    (static / "main.1234abcd.js").write_bytes(source)
    (static / "main.1234abcd.js.gz").write_bytes(gzip.compress(source, mtime=0))
    app = FastAPI()
    app.mount("/static", PrecompressedStaticFiles(directory=static))

    response = TestClient(app).get(
        "/static/main.1234abcd.js",
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.content == source
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "Accept-Encoding" in response.headers["vary"]


def test_precompressed_static_files_respect_zero_quality(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    source = b"body{}" * 300
    (static / "main.1234abcd.css").write_bytes(source)
    (static / "main.1234abcd.css.gz").write_bytes(gzip.compress(source, mtime=0))
    app = FastAPI()
    app.mount("/static", PrecompressedStaticFiles(directory=static))

    response = TestClient(app).get(
        "/static/main.1234abcd.css",
        headers={"Accept-Encoding": "gzip;q=0, identity"},
    )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


def test_precompressed_static_files_respect_client_quality_order(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    source = b"window.__quality = true;" * 300
    (static / "main.1234abcd.js").write_bytes(source)
    (static / "main.1234abcd.js.gz").write_bytes(gzip.compress(source, mtime=0))
    # The Brotli body is deliberately not decoded in this case: gzip has the
    # higher client quality and therefore must be selected.
    (static / "main.1234abcd.js.br").write_bytes(b"unused-brotli-variant")
    app = FastAPI()
    app.mount("/static", PrecompressedStaticFiles(directory=static))

    response = TestClient(app).get(
        "/static/main.1234abcd.js",
        headers={"Accept-Encoding": "br;q=0.4, gzip;q=1"},
    )

    assert response.status_code == 200
    assert response.content == source
    assert response.headers["content-encoding"] == "gzip"
