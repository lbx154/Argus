@echo off
rem One screen on an Argus project from its record: requires Python 3 on PATH
python -c "import sys; sys.exit(sys.version_info[0] != 3)" >nul 2>&1
if errorlevel 1 (
  >&2 echo argus-digest requires Python 3 on PATH as python.
  exit /b 127
)
python -X utf8 "%~dp0argus-digest" %*
