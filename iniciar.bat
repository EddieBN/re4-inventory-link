@echo off
title RE4 Inventory Link
chcp 65001 >nul
cd /d "%~dp0"
python server.py %*
pause
