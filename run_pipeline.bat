@echo off
rem Runs the Instagram->Vault pipeline (called by Task Scheduler or manually).
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0pipeline.py"
exit /b %ERRORLEVEL%
