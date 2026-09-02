import unittest
import io
from unittest.mock import patch

from backend.core.auth.client import NEUAuthClient


class WebVPNCaptchaTests(unittest.TestCase):
    def test_extracts_second_auth_form_and_relative_captcha(self):
        html = """
        <form id="second_auth_form" action="/tpass/secondAuth">
          <input type="hidden" name="RelayState" value="relay">
          <input type="hidden" name="execution" value="exec">
          <input name="method" value="mobile">
          <img src="code?ts=1">
        </form>
        """
        result = NEUAuthClient._extract_second_auth_form(
            html, "https://webvpn.neu.edu.cn/https/token/tpass/login"
        )
        self.assertEqual(result["form_action"], "https://webvpn.neu.edu.cn/tpass/secondAuth")
        self.assertEqual(result["captcha_url"], "https://webvpn.neu.edu.cn/https/token/tpass/code?ts=1")
        self.assertEqual(result["hidden_fields"]["execution"], "exec")

    def test_ocr_result_is_limited_to_short_numeric_prefill(self):
        from backend.core.auth import captcha

        with patch.object(captcha, "_get_ocr") as get_ocr:
            get_ocr.return_value.classification.return_value = "O1s4"
            result = captcha.recognize_numeric_captcha(b"image")
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "0154")
        self.assertLess(result["confidence"], 0.9)

    def test_ocr_rejects_arbitrary_text(self):
        from backend.core.auth import captcha

        with patch.object(captcha, "_get_ocr") as get_ocr:
            get_ocr.return_value.classification.return_value = "not-a-code"
            result = captcha.recognize_numeric_captcha(b"image")
        self.assertFalse(result["ok"])
        self.assertEqual(result["value"], "")

    def test_bundled_model_is_offline_and_numeric(self):
        from backend.core.auth import captcha

        try:
            ocr = captcha._get_ocr()
        except RuntimeError as error:
            self.skipTest(str(error))
        self.assertEqual(captcha.recognizer_status()["mode"], "cpu_numeric_onnx")
        self.assertTrue(captcha._MODEL_PATH.is_file())

        from PIL import Image, ImageDraw, ImageFont

        # Use Pillow's bundled font so this release guard exercises the same
        # OCR path on Windows and Linux runners without relying on OS fonts.
        font = ImageFont.load_default(size=28)
        expected = "6726"
        image = Image.new("RGB", (110, 44), "white")
        ImageDraw.Draw(image).text((8, 3), expected, font=font, fill="black")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        result = captcha.recognize_numeric_captcha(payload.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], expected)
        self.assertNotIn("ddddocr", type(ocr).__module__)


if __name__ == "__main__":
    unittest.main()
