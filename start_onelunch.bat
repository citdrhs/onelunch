@echo off
cd /d "%~dp0"
title Onelunch
python app.py
if errorlevel 1 pause
