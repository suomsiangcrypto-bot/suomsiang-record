# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# รวมไฟล์ assets ของ customtkinter (ธีม/ฟอนต์) + ไลบรารีเสียง
datas = collect_data_files("customtkinter")

# ฝัง ffmpeg.exe ไปด้วย ถ้ามีไฟล์อยู่ในโฟลเดอร์ (GitHub Actions จะดาวน์โหลดให้)
binaries = []
if os.path.exists("ffmpeg.exe"):
    binaries.append(("ffmpeg.exe", "."))

# DLL ของ pyaudiowpatch (portaudio) สำหรับอัดเสียงระบบ
try:
    binaries += collect_dynamic_libs("pyaudiowpatch")
    datas += collect_data_files("pyaudiowpatch")
except Exception:
    pass

block_cipher = None

a = Analysis(
    ["suemsiang_recorder.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["customtkinter", "pyaudiowpatch"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SuemsiangRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # ไม่มีหน้าต่าง command line ดำ ๆ
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
