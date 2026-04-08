@echo off
echo ============================================
echo   PyKV - Starting PRIMARY server on 8000
echo ============================================
set REPLICA_URLS=http://127.0.0.1:8001
uvicorn main_v2:app --port 8000
pause
