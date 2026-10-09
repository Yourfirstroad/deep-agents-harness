@echo off
rem 启动 langgraph dev：使用项目虚拟环境，并开启 Python UTF-8 模式
rem （UTF-8 模式用于修复中文 Windows 上 GBK 编码导致的启动崩溃）
set PYTHONUTF8=1
"%~dp0.venv\Scripts\langgraph.exe" dev %*
