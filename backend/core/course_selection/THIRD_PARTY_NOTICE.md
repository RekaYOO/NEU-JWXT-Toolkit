# Course weight optimizer: third-party notice

`weight_optimizer.py` contains a modified mathematical core from
[rtb-1005/Course_Weight-Optimizer](https://github.com/rtb-1005/Course_Weight-Optimizer),
fixed at commit `d70349b1e8cd5bef2ab73bdcce712614813243e6`.

The retained ideas include end-of-round demand forecasting, SAFE/COMP
classification, an exponential probability proxy, and water-filling weight
allocation. NEU-JWXT-Toolkit adds integer JWXK weights, user-defined plan-group
targets, timetable conflict constraints, bounded search, current selections,
persistence, and background recalculation. The proxy values are not calibrated
admission probabilities.

## Upstream license

MIT License

Copyright (c) 2026 rtb-1005

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## WebVPN CAPTCHA prefill

The WebVPN numeric CAPTCHA helper bundles only the `common_old.onnx` OCR model
from `ddddocr` 1.6.1 (MIT). The Python runtime adapter is implemented locally
with Pillow, NumPy and ONNX Runtime; the full `ddddocr` package, detection
model and slide-recognition model are not shipped. The model is used only to
prefill an editable field; it does not submit a CAPTCHA or send an SMS. The
upstream source and license are available at
https://github.com/sml2h3/ddddocr. The bundled model remains a third-party
component under the upstream MIT license.
