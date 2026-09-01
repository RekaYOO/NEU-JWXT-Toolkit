"""Small, offline numeric CAPTCHA helper used by WebVPN second-auth.

The recognizer is intentionally not a general OCR service. It loads one
MIT-licensed ddddocr ``common_old`` ONNX model, restricts CTC decoding to the
ten digit classes, and only returns an editable suggestion. It never sends
SMS or makes an authentication decision.
"""

from __future__ import annotations

import io
import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OCR_LOCK = threading.Lock()
_OCR_INSTANCE: Any | None = None
_OCR_ERROR: str | None = None
_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="captcha-ocr")

# Indexes in ddddocr's MIT common_old.onnx charset. Index 0 is CTC blank.
_DIGIT_INDEX_TO_CHAR = {
    6749: "0", 4410: "1", 78: "2", 7721: "3", 5806: "4",
    6977: "5", 5961: "6", 409: "7", 6979: "8", 2879: "9",
}
_MODEL_PATH = Path(__file__).with_name("models") / "common_old.onnx"
_MODEL_SHA256 = "b8f2ad9cbc1f2e3922a6cb9459e30824e7e2467f3fb4fd61420640e34ea0bf68"
_LOOKALIKE_DIGITS = str.maketrans({
    "o": "0", "O": "0", "q": "0", "Q": "0",
    "i": "1", "I": "1", "l": "1", "L": "1",
    "s": "5", "S": "5", "b": "8",
})


class _NumericOnnxOCR:
    """Minimal adapter around the single CPU ONNX model."""

    def __init__(self, model_path: Path):
        import onnxruntime as ort

        ort.set_default_logger_severity(4)
        options = ort.SessionOptions()
        # The upstream model advertises a symbolic sequence length and emits
        # a harmless shape warning for each dynamic-width inference. Keep that
        # implementation detail out of the user's console/log stream.
        options.log_severity_level = 4
        self._session = ort.InferenceSession(
            str(model_path), options=options, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    @staticmethod
    def _prepare(image: bytes):
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(image)) as source:
            gray = source.convert("L")
            target_height = 64
            target_width = max(1, int(gray.width * target_height / gray.height))
            gray = gray.resize((target_width, target_height), Image.Resampling.LANCZOS)
            array = np.asarray(gray, dtype=np.float32) / 255.0
        return array[None, None, :, :]

    def classification(self, image: bytes) -> str:
        import numpy as np

        output = self._session.run(None, {self._input_name: self._prepare(image)})[0]
        if output.ndim != 3:
            raise ValueError("unexpected OCR output rank")
        logits = output[:, 0, :] if output.shape[1] == 1 else output[0, :, :]
        predictions = np.argmax(logits, axis=1)
        result: list[str] = []
        previous: int | None = None
        for predicted in predictions:
            index = int(predicted)
            if index != previous and index in _DIGIT_INDEX_TO_CHAR:
                result.append(_DIGIT_INDEX_TO_CHAR[index])
            previous = index
        return "".join(result)


def _get_ocr() -> Any:
    global _OCR_INSTANCE, _OCR_ERROR
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE
    if _OCR_ERROR:
        raise RuntimeError(_OCR_ERROR)
    with _OCR_LOCK:
        if _OCR_INSTANCE is not None:
            return _OCR_INSTANCE
        if _OCR_ERROR:
            raise RuntimeError(_OCR_ERROR)
        try:
            if not _MODEL_PATH.is_file() or _MODEL_PATH.stat().st_size < 1024:
                raise FileNotFoundError("内置验证码模型不存在")
            digest = hashlib.sha256(_MODEL_PATH.read_bytes()).hexdigest()
            if digest != _MODEL_SHA256:
                raise ValueError("内置验证码模型校验失败")
            _OCR_INSTANCE = _NumericOnnxOCR(_MODEL_PATH)
            return _OCR_INSTANCE
        except Exception as exc:  # pragma: no cover - platform/dependency specific
            _OCR_ERROR = f"OCR 初始化失败（{type(exc).__name__}）"
            logger.warning("WebVPN CAPTCHA OCR unavailable: %s", type(exc).__name__)
            raise RuntimeError(_OCR_ERROR) from exc


def recognizer_status() -> dict[str, Any]:
    """Return non-sensitive OCR availability information."""
    return {
        "available": _OCR_INSTANCE is not None or _OCR_ERROR is None,
        "loaded": _OCR_INSTANCE is not None,
        "error": _OCR_ERROR,
        "mode": "cpu_numeric_onnx",
        "model": _MODEL_PATH.name,
    }


def warmup_captcha_ocr() -> None:
    """Warm the singleton in a background thread; manual entry is always a fallback."""
    try:
        _get_ocr()
    except Exception:
        return


def recognize_numeric_captcha(image_bytes: bytes) -> dict[str, Any]:
    """Return an editable numeric suggestion, never an authentication result."""
    if not image_bytes:
        return {"ok": False, "value": "", "confidence": 0.0, "reason": "empty_image"}
    started = time.perf_counter()
    try:
        future = _OCR_EXECUTOR.submit(_get_ocr().classification, image_bytes)
        try:
            raw = str(future.result(timeout=1.0) or "").strip()
        except FutureTimeout:
            logger.warning("WebVPN CAPTCHA OCR timed out")
            return {
                "ok": False, "value": "", "confidence": 0.0,
                "reason": "ocr_timeout",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        normalized = raw.translate(_LOOKALIKE_DIGITS)
        value = "".join(ch for ch in normalized if ch.isdigit())
        if not 3 <= len(value) <= 8:
            return {
                "ok": False, "value": "", "confidence": 0.0,
                "reason": "unexpected_length",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        return {
            "ok": True, "value": value,
            "confidence": 0.94 if normalized == raw else 0.72,
            "reason": "recognized",
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        logger.warning("WebVPN CAPTCHA OCR failed: %s", type(exc).__name__)
        return {
            "ok": False, "value": "", "confidence": 0.0,
            "reason": "ocr_error",
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
