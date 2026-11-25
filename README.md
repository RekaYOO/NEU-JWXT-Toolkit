<p align="center"><img src="assets/branding/app-icon.png" width="88" alt="NEU 教务工具箱图标"></p>

# NEU 教务系统工具箱

[![下载最新版本](https://img.shields.io/github/v/release/RekaYOO/NEU-JWXT-Toolkit?label=Release)](https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest)
[![平台构建](https://github.com/RekaYOO/NEU-JWXT-Toolkit/actions/workflows/branch-delivery.yml/badge.svg?branch=main)](https://github.com/RekaYOO/NEU-JWXT-Toolkit/actions/workflows/branch-delivery.yml?query=branch%3Amain)

面向东北大学学生的第三方教务工具箱。查成绩和专业排名、规划剩余学分、查大纲和考核方式、排课选课、打包下载活动证明，都可以在一个地方完成。

[下载与安装](#下载与安装) · [功能演示](#功能演示) · [使用手册](docs/README.md) · [旧版资料导入](docs/部署/旧版资料导入.md)

## 功能演示

以下画面来自实际页面，使用同一套虚构的课程、成绩、学分进度和活动数据。点击图片可放大。

### 查成绩，规划剩余学分

| [成绩明细](docs/功能/成绩查询.md) | [培养计划](docs/功能/培养计划.md) |
| --- | --- |
| [![成绩明细展示多学期筛选、绩点和专业排名](assets/demo/scores.png)](assets/demo/scores.png) | [![培养计划展示分类进度、已修课程与剩余学分](assets/demo/academic-plan.png)](assets/demo/academic-plan.png) |
| 按学期和课程类型筛选，查看分项成绩、官方绩点和学校提供的专业排名。 | 按类别看已修、在修和待修课程，区分必修、选修学分缺口，点开课程查看大纲。 |

### 看周课表，整理个人日程

[![周三第 3–4 节叠放三门不同颜色的课程，每门标有冲突，个人日程与课程同屏](assets/demo/timetable.png)](assets/demo/timetable.png)

周三第 3–4 节的三门课叠放在同一格，悬停或用键盘聚焦可切换展开项，点击查看详情。课程按科目分色，学习小组和实验报告提醒也排在课表上。[查询课表使用说明](docs/功能/查询课表.md) · [手机端当日日程预览](assets/demo/timetable-agenda-mobile.png)

### 模拟绩点，准备选课

| [GPA 模拟器](docs/功能/GPA模拟器.md) | [选课工作台](docs/功能/选课系统.md) |
| --- | --- |
| [![GPA 模拟器展示预估成绩与学分](assets/demo/gpa-simulator.png)](assets/demo/gpa-simulator.png) | [![选课工作台展示课程列表和方案组](assets/demo/course-selection.png)](assets/demo/course-selection.png) |
| 从已有成绩和培养计划导入课程，调整预估成绩，比较并保存方案。 | 按课程和班次查找，整理方案组、预览课表与检查冲突。抢选、空位换课和权重投放按学校轮次、账号资格与用户配置执行。 |

### 查考核方式，整理活动证明

| [课程大纲](docs/功能/课程大纲.md) | [四节活动与证明导出](docs/功能/导出中心与四节活动.md) |
| --- | --- |
| [![课程大纲展示教材、学时和课程简介](assets/demo/course-outline.png)](assets/demo/course-outline.png) | [![四节活动展示活动记录与证书打包入口](assets/demo/festival-certificates.png)](assets/demo/festival-certificates.png) |
| 按课程名、开课单位、学分和学时查找，读教学内容与考核方式，下载 HTML 或学校提供的附件。 | 汇总创意节、科普节、科技节和创业节记录，查看参与详情，按日期打包已颁发的证书。 |

还可使用[成绩变动邮件](docs/功能/成绩追踪.md)、[考试与 ICS 导出](docs/功能/我的考试.md)、[实验选课](docs/功能/实验选课.md)、[科研训练](docs/功能/科研训练.md)、[评教](docs/功能/自动评教.md)和[离线查看](docs/功能/离线使用.md)。

## 下载与安装

从 [GitHub Releases](https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest) 获取安装包，并用同页的 `SHA256SUMS.txt` 核对文件。

| 使用场景 | 下载与启动 |
| --- | --- |
| Windows x64 | 解压 `*-windows-amd64-go.zip`，双击 `启动工具箱.exe`。在系统设置检查更新。 |
| Linux x64 服务端 | 使用下方安装命令。支持 HTTPS 反向代理、按账号准入、升级和回退。 |
| Android 本地版 | 安装 `*-android-local-arm64.apk`。内置本地服务，数据保留在手机上。 |
| Android 远程客户端 | 安装 `*-android-client-arm64.apk`，填写自己的 HTTPS 服务地址。 |

两个 APK 支持 Android 7.0 及以上的 `arm64-v8a` 设备。下载、更新与通知权限见[Android 安装](docs/部署/Android安装.md)。

### Linux 一键维护

适用于有 systemd 的 x64 主机，需 Bash、`curl`、Python 3.11+ 和 `sudo`。准备 HTTPS 域名与反向代理。

```bash
curl -fL --retry 3 --proto '=https' -o install.sh https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest/download/install.sh && bash ./install.sh install
```

已有服务时，使用同一个脚本执行 `bash ./install.sh update`。也可用 `status` 查看服务状态、`recover` 恢复中断的维护、`rollback --restore-data` 切回上一个检查点。Windows 更新、Linux 安装参数、备份与旧资料迁移见[安装与维护](docs/部署/Go运行包.md)。

## 登录与使用

- 校内可直连，校外选 WebVPN。两条线路都可使用学号密码、智慧东大扫码或微信扫码。等待密码连接时可以取消，短信验证由用户主动发起。详见[校外访问说明](docs/功能/WebVPN校外访问.md)。
- 已缓存的数据可在离线模式查看。涉及成绩、学分、选课资格及考试安排，请以学校最终结果为准。
- 选课、退课、评教和证明生成等远端提交需要确认。遇到结果不明确的提交，先核对官方结果再决定是否重试。

## 源码运行与贡献

Windows 和 Linux 后端采用 Go 与 SQLite，前端使用 React、Ant Design 和 Vite。使用 Go 1.24+ 与 Node.js 22，在仓库根目录执行：

```bash
npm --prefix frontend ci
npm --prefix frontend run build
go run ./cmd/toolbox --mode local-single --listen 127.0.0.1:8000 --data ./data-go
```

打开 `http://127.0.0.1:8000`。贡献前可查阅[开发接手与变更规范](docs/开发/开发接手与变更规范.md)、[发行流程](docs/开发/发行与CI.md)以及[演示图生成说明](assets/demo/README.md)。

本项目采用 [MIT License](LICENSE)，与东北大学及其教务系统运营单位无隶属关系。请遵守学校规定并妥善保管账号、会话和本地数据。
