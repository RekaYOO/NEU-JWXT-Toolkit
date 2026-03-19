# NEU 教务系统工具箱

NEU教务系统第三方工具箱，提供成绩查询、培养计划查看、实验选课、GPA 模拟等功能。并正在随教务的更新而更新

保留了原汁原味的拼音+英语命名法(bushi)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev)

---

## ✨ 功能特性（目前）

| 功能 | 说明 |
|------|------|
| 📊 **成绩查询** | 自动获取所有学期成绩，支持离线查看 |
| 📋 **培养计划** | 查看学业监测报告，更便捷的学分完成度统计 |
| 🔬 **实验选课** | 在线选择/退选实验课程和班级 |
| 📈 **GPA 模拟** | 模拟不同成绩对 GPA 的影响，支持保存方案 |
---

## 🚀 快速开始

### 环境要求

- Python 3.8+
- Node.js 16+

### 安装

```bash
# 克隆项目
git clone https://github.com/RekaYOO/NEU-JWXT-Toolkit.git
cd NEU-JWXT-Toolkit

# 安装 Python 依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 安装前端依赖
cd frontend && npm install && cd ..
```

### 启动

```bash
# 一键启动前后端
python start_all.py
```

或分别启动：

```bash
# 终端1：后端
.venv\Scripts\python backend\main.py

# 终端2：前端
cd frontend && npm start
```

### 访问

- 前端界面: http://localhost:3000
- API 文档: http://localhost:8000/docs

---

## 📖 使用指南

### 首次使用

1. 打开 http://localhost:3000
2. 输入学号和密码登录
3. 勾选"记住密码"可实现自动登录
4. 成绩数据会自动保存到本地

### 成绩查询

- 查看所有学期成绩列表
- 支持按课程名称、学期、成绩等筛选排序
- 点击列设置按钮自定义显示列
- 点击刷新按钮获取最新数据

### 培养计划

- 查看学业监测报告
- 学分完成度统计
- 按类别查看课程（通识类、学科基础类等）
- 已通过/已选课/未修读状态一目了然

### 实验选课

- 查看当前学期可选实验课程
- 展开课程查看实验项目
- 选择实验班（查看时间、地点、容量）
- 已选班级可直接退选

### GPA 模拟

1. 在成绩页面点击"GPA模拟"按钮
2. 修改课程成绩查看 GPA 变化
3. 从培养计划导入未修课程进行预估
4. 点击保存按钮保存模拟方案
5. 可随时加载历史方案进行对比

### 离线使用

1. 首次登录后数据已保存到本地 `data/` 目录
2. 下次启动可直接查看本地数据
3. 点击"刷新数据"获取最新成绩

---

## 📚 文档

**开发指南：**
- [快速开始](QUICKSTART.md) - 详细安装和启动指南
- [前端规范](docs/guides/FRONTEND.md) - 前端开发规范
- [架构设计](docs/guides/ARCHITECTURE.md) - 项目架构说明
- [培养计划解析](docs/reference/ACADEMIC_REPORT.md) - 培养计划数据结构详解

**API 参考：**
- [后端 API](docs/reference/API.md) - REST API 接口文档
- [外部接口](docs/reference/EXTERNAL_APIS.md) - 教务系统接口汇总
- [字段映射](docs/reference/FIELD_MAPPING.md) - 接口字段对照表

**部署运维：**
- [部署指南](docs/deployment/DEPLOYMENT.md) - 生产环境部署
- [免密登录](docs/deployment/CAS_COOKIE_PERSISTENCE.md) - Cookie 持久化机制

**完整文档索引：** [docs/README.md](docs/README.md)

---

## ⚠️ 免责声明

本项目为第三方工具，与学校无关。仅供学习交流使用，请遵守学校相关规定，合理使用本工具。使用本工具产生的任何后果由使用者自行承担。本工具的所有服务仅在本地进行。

