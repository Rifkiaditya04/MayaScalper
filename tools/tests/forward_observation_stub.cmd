@echo off
setlocal

if /I "%~3"=="preflight" goto preflight
if /I "%~3"=="start" goto start
goto unexpected

:preflight
echo STUB preflight exit=0
exit /b 0

:start
if /I "%TSP_SUPERVISOR_STUB_MODE%"=="success" goto start_success
if /I "%TSP_SUPERVISOR_STUB_MODE%"=="fail_once" goto start_fail_once
echo STUB start exit=3
exit /b 3

:start_fail_once
if exist "%TSP_SUPERVISOR_STUB_STATE_FILE%" goto start_success
echo failed > "%TSP_SUPERVISOR_STUB_STATE_FILE%"
echo STUB start first_attempt exit=3
exit /b 3

:start_success
echo STUB start exit=0
exit /b 0

:unexpected
echo STUB unexpected_command=%~3
exit /b 64
