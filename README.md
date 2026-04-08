# 成长记录网站

记录孩子成长过程中的每一个美好瞬间。

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 添加照片
将照片文件放入 `photos` 文件夹，支持格式：JPG、PNG、GIF、WebP

### 3. 启动网站
```bash
python server.py
```
或双击 `启动.bat`

### 4. 访问
打开浏览器访问 http://localhost:8000

## 功能特点

- **时间轴展示** - 按年月自动整理照片
- **缩略图优化** - 自动生成缩略图，加快加载速度
- **照片描述** - 可以为每张照片添加文字记录
- **懒加载** - 只加载可见区域的照片，节省流量
- **响应式设计** - 支持手机、平板、电脑访问

## 照片存储

- 原始照片：`photos/` 文件夹
- 缩略图：`thumbnails/` 文件夹（自动生成）
- 数据库：`photos.db`

## 部署到服务器

部署到服务器时需要：

1. 上传所有文件到服务器
2. 安装依赖：`pip install -r requirements.txt`
3. 将照片上传到 `photos` 目录
4. 使用 nginx 反向代理或直接运行：`nohup python server.py &`
5. 配置防火墙开放 8000 端口