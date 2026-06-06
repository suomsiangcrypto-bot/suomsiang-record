@echo off
chcp 65001 >nul
title สร้างไฟล์ EXE - สุ่มเสี่ยงบันทึก
echo ============================================
echo   กำลังสร้างไฟล์ EXE ... กรุณารอสักครู่
echo ============================================
echo.

echo [1/3] ติดตั้งไลบรารีที่จำเป็น...
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
echo.

echo [2/3] (ทางเลือก) วาง ffmpeg.exe ไว้ในโฟลเดอร์นี้
echo       เพื่อให้ exe พร้อมใช้โดยไม่ต้องติดตั้ง FFmpeg แยก
echo.

echo [3/3] กำลังคอมไพล์...
pyinstaller suemsiang_recorder.spec
echo.

echo ============================================
echo   เสร็จแล้ว! ไฟล์อยู่ที่:  dist\SuemsiangRecorder.exe
echo ============================================
pause
