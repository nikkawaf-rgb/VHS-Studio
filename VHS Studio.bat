@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" pythonw -m vhs_studio
