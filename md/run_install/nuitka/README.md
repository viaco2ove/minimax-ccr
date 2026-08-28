# Nuitka 预安装
**Nuitka 预先安装** conda &  python=3.12  & Visual Studio 2022 Build Tools
echo winget install Microsoft.VisualStudio.2022.BuildTools
echo conda create -n ccrg312 python=3.12 -y
echo conda activate ccrg312
echo pip install fastapi uvicorn httpx ...

# 运行
cmd
cd /d $minimax-ccr
python build_nuitka.py

powerShell
cd /d $minimax-ccr
python build_nuitka.py