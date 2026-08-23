# minimax-ccr-run 部署

## 方式一：同步脚本（日常开发用）

修改源码后，双击同步：

```
md/run_install/sync_to_run.bat
```

然后重启 run 环境：
```bash
cd D:\Users\viaco\PycharmProjects\minimax-ccr-run
.venv\Scripts\python.exe -m ccrg.main
```

## 方式二：打包成 exe

需要先安装 PyInstaller：
```bash
pip install pyinstaller
```

然后打包：
```bash
cd D:\Users\viaco\PycharmProjects\minimax-ccr
pyinstaller ccrg.spec --clean
```

exe 文件输出到 `dist\ccrg\ccrg.exe`，直接运行即可。

打包前先同步源码（方式一），确保 exe 里是最新的代码。

## 配置文件

配置文件位置：
- `.gateway.json` — provider、routing、workflow 配置
- `keywords.json` — 关键词路由配置

修改后重启服务即可生效。

**注意**：exe 打包后，配置文件需要跟随 exe 放在同一目录下。