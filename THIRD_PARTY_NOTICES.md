# Third-party notices

## Course_Weight-Optimizer

The JWXK weight-allocation model in
`backend/core/course_selection/weight_optimizer.py` is adapted from
`rtb-1005/Course_Weight-Optimizer`, commit
`d70349b1e8cd5bef2ab73bdcce712614813243e6`.

The upstream forecasting, SAFE/COMP classification, exponential probability
proxy, and water-filling allocation are retained. This project adds integer
JWXK weights, user-defined group quotas, conflict constraints, bounded search,
already-selected courses, persistence, and background recalculation.

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
