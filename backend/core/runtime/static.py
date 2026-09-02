"""Static delivery helpers shared by source and frozen runtimes."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders


_HASHED_ASSET = re.compile(r"\.[0-9a-f]{8,}\.", re.IGNORECASE)
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
REVALIDATE_CACHE_CONTROL = "no-cache"


def _append_vary(headers: MutableHeaders, value: str) -> None:
    existing = [item.strip() for item in headers.get("vary", "").split(",") if item.strip()]
    if value.lower() not in {item.lower() for item in existing}:
        existing.append(value)
    headers["Vary"] = ", ".join(existing)


def _encoding_quality(value: str, encoding: str) -> float:
    wildcard_quality: float | None = None
    for item in value.lower().split(","):
        name, *parameters = (part.strip() for part in item.split(";"))
        quality = 1.0
        for parameter in parameters:
            if parameter.startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        if name == encoding:
            return max(0.0, min(1.0, quality))
        if name == "*":
            wildcard_quality = quality
    return max(0.0, min(1.0, wildcard_quality or 0.0))


class PrecompressedStaticFiles(StaticFiles):
    """Serve prebuilt Brotli/gzip variants without changing asset URLs."""

    async def get_response(self, path: str, scope):
        request_headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }
        accepted = request_headers.get("accept-encoding", "").lower()
        encoding = ""
        served_path = path
        directory = Path(str(self.directory))
        candidates = sorted(
            (
                (_encoding_quality(accepted, "br"), 1, "br", ".br"),
                (_encoding_quality(accepted, "gzip"), 0, "gzip", ".gz"),
            ),
            reverse=True,
        )
        for quality, _preference, candidate_encoding, suffix in candidates:
            if quality <= 0:
                continue
            candidate = directory / f"{path}{suffix}"
            try:
                candidate.resolve().relative_to(directory.resolve())
            except (OSError, ValueError):
                continue
            if candidate.is_file():
                encoding = candidate_encoding
                served_path = f"{path}{suffix}"
                break

        response = await super().get_response(served_path, scope)
        headers = response.headers
        if response.status_code in {200, 206, 304}:
            headers["Cache-Control"] = (
                IMMUTABLE_CACHE_CONTROL if _HASHED_ASSET.search(Path(path).name)
                else REVALIDATE_CACHE_CONTROL
            )
            if encoding:
                headers["Content-Encoding"] = encoding
                content_type, _ = mimetypes.guess_type(path)
                if content_type:
                    headers["Content-Type"] = content_type
                _append_vary(headers, "Accept-Encoding")
        return response
