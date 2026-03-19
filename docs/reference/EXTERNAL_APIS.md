# 外部教务系统接口文档

本文档汇总 NEU 教务系统工具箱对接的所有外部接口。

## 接口列表

### CAS 认证
- `POST https://pass.neu.edu.cn/tpass/login` - 登录

### 成绩查询
- `GET /jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcjxnxq.do` - 学期列表
- `POST /jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do` - 成绩列表
- `GET /jwapp/sys/cjzhcxapp/api/wdcj/queryPjxfjd.do` - 总绩点

### 培养计划
- `POST /jwapp/sys/byshapp/api/grbg/queryXyzhbx.do` - 学业监测报告

### 实验选课
- `POST /jwapp/sys/syxkapp/api/xsxk/queryCanSelectedCourses.do` - 课程列表

完整文档内容较多，请参考代码实现或联系维护者获取详细文档。
