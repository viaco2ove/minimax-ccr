# minimax-ccr-run 安装说明

## 环境说明

- **开发环境**（3429）：`$minimax-ccr` — 修改源代码
- **运行环境**（3428）：`$minimax-ccr-run` — 实际运行服务

## 两种部署方式

### 方式一：源码同步安装（推荐开发时使用）

将 minimax-ccr 以 editable 模式安装到 run 的虚拟环境，修改源代码后立即生效，无需手动复制。

```bash
# 安装/更新
$minimax-ccr-run\.venv\Scripts\python.exe -m pip install -e $minimax-ccr --quiet

# 验证安装（显示路径说明装好了）
$minimax-ccr-run\.venv\Scripts\python.exe -c "import ccrg; print(ccrg.__file__)"
```

### 方式二：手动复制代码

当需要在某台机器独立部署、不依赖源码时使用。

1. 将 `src/ccrg/` 整个目录复制到 `$minimax-ccr-run\src\`
2. 重启服务

## 重启服务

```bash
# 进入 run 环境
cd $minimax-ccr-run

# 停止旧服务（如果正在运行），然后启动
.venv\Scripts\python.exe -m ccrg.main
```

## 调试

日志文件：`$minimax-ccr\logs\ccrg.log`

网关日志目录：`$minimax-ccr\logs\req\` — 包含每个请求的完整发送 JSON

## 常见问题

**Q: 修改代码后没生效？**
A: 确认用的是 run 虚拟环境里的 python，重启服务。

**Q: 只想临时测试某个文件改动了不影响原环境？**
A: 用方式一，改动通过 pip install -e 直接生效，测试完记得 revert 源码改动。

**Q: 想在开发环境也装一份？**
A: 在 minimax-ccr 目录下直接运行：
```bash
pip install -e .
```
