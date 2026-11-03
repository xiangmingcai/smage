@echo off
rem Double-click this file to start Smage.
rem It uses the project's own virtual environment (pythonw = no console window).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0src\smage\main.py"
