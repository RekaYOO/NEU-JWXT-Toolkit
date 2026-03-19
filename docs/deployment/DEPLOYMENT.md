# 部署文档 (Deployment)

## 1. 项目结构

```
e:\code\NEUT\
├── backend\                     # FastAPI 后端
│   ├── main.py                  # 主服务
│   └── requirements.txt         # 依赖
│
├── frontend\                    # React 前端
│   ├── public\                  # 静态文件
│   ├── src\                     # 源代码
│   │   ├── components\         # 组件
│   │   ├── layouts\            # 布局
│   │   ├── pages\              # 页面
│   │   ├── services\           # API服务
│   │   ├── App.js             # 主应用
│   │   └── index.js           # 入口
│   └── package.json            # 依赖
│
├── docs\                        # 开发文档
├── neu_auth\                    # 认证模块
├── neu_academic\               # 成绩模块
├── neu_storage\                # 存储模块
└── data\                        # 本地数据（运行后生成）
```

## 2. 启动后端

```bash
cd e:\code\NEUT\backend

# 创建虚拟环境（推荐）
python -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动服务
python main.py
```

服务将运行在: http://localhost:8000

API 文档: http://localhost:8000/docs

## 3. 启动前端

```bash
cd e:\code\NEUT\frontend

# 安装依赖
npm install

# 启动开发服务器
npm start
```

前端将运行在: http://localhost:3000

## 4. 使用说明

### 首次使用
1. 打开 http://localhost:3000
2. 输入学号和密码登录
3. 勾选"记住密码"可自动登录
4. 成绩数据会自动保存到本地

### 离线使用
1. 登录后数据已保存到本地
2. 下次可直接查看本地成绩
3. 点击"刷新数据"获取最新成绩

### 数据存储位置
- 本地数据存储在项目根目录的 `data/` 文件夹中
- 包含：成绩CSV、配置JSON、登录凭证

## 5. 生产部署

### 后端部署
```bash
# 使用 gunicorn
pip install gunicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

### 前端部署
```bash
# 构建生产版本
npm run build

# 部署 dist/ 目录到静态服务器
```

## 6. 端口配置

| 服务 | 端口 | 配置方式 |
|------|------|----------|
| 后端 | 8000 | main.py 或环境变量 |
| 前端 | 3000 | npm start 默认 |

修改前端代理：
```json
// frontend/package.json
"proxy": "http://localhost:8000"
```
