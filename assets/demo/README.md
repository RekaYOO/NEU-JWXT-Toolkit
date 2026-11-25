# README 截图

运行以下命令可用 Chrome 重新生成演示图。

```bash
npm --prefix frontend ci
npm --prefix frontend run build
node tools/capture_readme_demo.cjs
```

脚本在回环地址启动生产页面，拦截所有 API 和外部请求。课程、学号、学期、成绩、排名与活动均为虚构示例。请勿使用真实账号、导出的个人数据或学校响应更新这些图片。
