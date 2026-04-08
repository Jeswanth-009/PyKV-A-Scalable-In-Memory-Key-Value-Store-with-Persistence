@echo off
echo ============================================
echo   PyKV - Installing dependencies...
echo ============================================
pip install fastapi uvicorn aiofiles httpx requests pydantic
echo.
echo ============================================
echo   Done! Now run:
echo   1. start_standby.bat  (first)
echo   2. start_primary.bat  (second)
echo ============================================
pause
