# CCRG EXE 打包与使用

## 生成 EXE

双击运行打包脚本：

```
md\run_install\bulid_exe.bat
```
  
打包完成后，产物在 `dist\ccrg\` 目录。

## 使用

直接双击 `dist\ccrg\ccrg.exe` 或命令行运行：

```cmd
cd dist\ccrg
ccrg.exe
```

启动后服务地址：`http://127.0.0.1:3428`

## 配置

编辑 `dist\ccrg\.gateway.json`，修改后重启 exe 生效。

## 验证

```cmd
curl http://127.0.0.1:3428/health
```
