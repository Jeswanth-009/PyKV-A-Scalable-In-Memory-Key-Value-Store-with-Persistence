@echo off
echo ============================================
echo   PyKV - Starting STANDBY server on 8001
echo ============================================
set IS_STANDBY=1
uvicorn main_v2:app --port 8001
pause
