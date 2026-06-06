# -*- coding: utf-8 -*-
"""
สุ่มเสี่ยงบันทึก  (Suemsiang Recorder)
โปรแกรมบันทึกหน้าจอ PC ความคมชัดสูงสุดระดับ 4K
- ปรับขนาดกรอบครอบจอได้ (ลากเลือกพื้นที่ / กรอกค่าเอง / เต็มจอ)
- บันทึกพร้อมเสียงระบบ + ไมโครโฟน (เลือกได้)
- บันทึกยาวไม่จำกัด (พัก/บันทึกต่อได้)
- หน้าตาทันสมัย โหมดมืด

ใช้ FFmpeg เป็นเอนจินเบื้องหลัง (ต้องติดตั้ง FFmpeg ก่อน)
รองรับ Windows เป็นหลัก (gdigrab + dshow) และมีโหมดสำรองสำหรับ Linux (x11grab)
"""

import os
import re
import sys
import time
import json
import shutil
import threading
import subprocess
import platform
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
except ImportError:
    print("กรุณาติดตั้ง customtkinter ก่อน:  pip install customtkinter")
    sys.exit(1)

# ไลบรารีอัดเสียงระบบแบบ WASAPI loopback (ไม่ต้องเปิด Stereo Mix)
try:
    import pyaudiowpatch  # noqa: F401
    HAS_PYAUDIO = True
except Exception:
    HAS_PYAUDIO = False


# ----------------------------------------------------------------------------
#  ค่าคงที่ / ธีมสี
# ----------------------------------------------------------------------------
APP_NAME = "สุ่มเสี่ยงบันทึก"
ACCENT = "#7C5CFC"          # ม่วงสด
ACCENT_HOVER = "#6B4BEB"
DANGER = "#FF4D6D"          # แดงสำหรับปุ่มบันทึก/หยุด
DANGER_HOVER = "#E63E5C"
SUCCESS = "#2ECC71"
BG_CARD = "#1C1C24"
BG_DEEP = "#121217"
TXT_DIM = "#8A8A99"

# ฟอนต์ UI — จะถูกตั้งเป็นฟอนต์ไทยที่คมชัดที่สุดตอนเปิดโปรแกรม
UI_FONT = "Tahoma"

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


# ----------------------------------------------------------------------------
#  ตัวช่วย FFmpeg
# ----------------------------------------------------------------------------
def app_dir():
    """โฟลเดอร์ของโปรแกรม (รองรับทั้งรันเป็นสคริปต์และเป็น .exe)"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundled_dir():
    """โฟลเดอร์ชั่วคราวที่ PyInstaller แตกไฟล์ออกมา (ถ้ามี)"""
    return getattr(sys, "_MEIPASS", app_dir())


def find_ffmpeg():
    """หาตำแหน่ง ffmpeg: ที่ฝังมากับ exe > ข้าง ๆ โปรแกรม > ใน PATH"""
    exe = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    for folder in (bundled_dir(), app_dir()):
        cand = os.path.join(folder, exe)
        if os.path.exists(cand):
            return cand
    p = shutil.which(exe)
    if p:
        return p
    return None


def list_dshow_audio_devices(ffmpeg):
    """คืนรายชื่ออุปกรณ์เสียง (Windows / dshow)"""
    if not (IS_WINDOWS and ffmpeg):
        return []
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, errors="ignore",
            creationflags=CREATE_NO_WINDOW,
        )
        out = proc.stderr or ""
    except Exception:
        return []

    devices = []
    in_audio = False
    for line in out.splitlines():
        low = line.lower()
        if "directshow audio devices" in low:
            in_audio = True
            continue
        if "directshow video devices" in low:
            in_audio = False
            continue
        if "alternative name" in low:
            continue
        m = re.search(r'"([^"]+)"', line)
        if not m:
            continue
        name = m.group(1)
        # FFmpeg รุ่นใหม่อาจต่อท้ายด้วย (audio)/(video) ในบรรทัดเดียวกัน
        if "(audio)" in low:
            devices.append(name)
        elif "(video)" in low:
            continue
        elif in_audio:
            devices.append(name)
    # ลบรายการซ้ำ คงลำดับเดิม
    seen, result = set(), []
    for d in devices:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def get_screen_geometry():
    """คืน (x, y, w, h) ของพื้นที่หน้าจอทั้งหมด"""
    if IS_WINDOWS:
        try:
            import ctypes
            u = ctypes.windll.user32
            u.SetProcessDPIAware()
            x = u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            y = u.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
            w = u.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
            h = u.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception:
            pass
    # fallback
    root = tk._default_root or tk.Tk()
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


WDA_EXCLUDEFROMCAPTURE = 0x00000011

def exclude_from_capture(window):
    """ทำให้หน้าต่าง 'มองเห็นด้วยตา แต่ไม่ติดในการบันทึกหน้าจอ' (Windows 10 2004+)
    คืน True ถ้าสำเร็จ"""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
        ok = ctypes.windll.user32.SetWindowDisplayAffinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE)
        return bool(ok)
    except Exception:
        return False


# ----------------------------------------------------------------------------
#  จำค่าตั้งค่า (บันทึกลงไฟล์ แล้วโหลดกลับตอนเปิดใหม่)
# ----------------------------------------------------------------------------
def config_path():
    if IS_WINDOWS and os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], "SuemsiangRecorder")
    else:
        base = os.path.join(os.path.expanduser("~"), ".suemsiang_recorder")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return os.path.join(base, "config.json")


def load_config():
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def pick_thai_font():
    """เลือกฟอนต์ที่เรนเดอร์ภาษาไทยได้คมชัดที่สุดเท่าที่มีในเครื่อง"""
    import tkinter.font as tkfont
    prefer = ["Leelawadee UI", "Leelawadee", "TH Sarabun New",
              "Tahoma", "Segoe UI"]
    try:
        fams = set(tkfont.families())
    except Exception:
        return "Tahoma"
    for f in prefer:
        if f in fams:
            return f
    return "Tahoma"


# ----------------------------------------------------------------------------
#  หน้าต่างลากเลือกพื้นที่ (Region selector)
# ----------------------------------------------------------------------------
class RegionSelector:
    """โอเวอร์เลย์ให้เลือกพื้นที่ แล้วลากปรับมุม/ขอบ และย้ายกรอบได้"""

    HANDLE = 9      # ครึ่งหนึ่งของขนาดจุดจับ (px)
    MIN = 40        # ขนาดต่ำสุดของกรอบ
    HIT = 12        # ระยะแตะจุดจับ

    def __init__(self, master, callback, initial=None):
        self.callback = callback
        self.mode = None        # nw,n,ne,e,se,s,sw,w | move | new | None
        self.anchor = None      # จุดเริ่มลากตอนวาดใหม่
        self.last = None        # ตำแหน่งเมาส์ล่าสุด (สำหรับโหมดย้าย)

        sx, sy, sw, sh = get_screen_geometry()
        self.off_x, self.off_y = sx, sy
        self.sw, self.sh = sw, sh

        # กรอบเริ่มต้น: กลางจอ 60% (ถ้ามีค่าเดิมส่งมา ใช้ค่านั้น)
        if initial:
            ix, iy, iw, ih = initial
            self.x0 = max(0, ix - sx); self.y0 = max(0, iy - sy)
            self.x1 = min(sw, self.x0 + iw); self.y1 = min(sh, self.y0 + ih)
        else:
            self.x0 = int(sw * 0.2); self.y0 = int(sh * 0.2)
            self.x1 = int(sw * 0.8); self.y1 = int(sh * 0.8)

        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.geometry(f"{sw}x{sh}+{sx}+{sy}")
        self.top.attributes("-alpha", 0.45)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#0A0A0F")

        self.canvas = tk.Canvas(self.top, bg="#0A0A0F", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # แถบควบคุม (ขนาด + ปุ่ม) ด้านบนกลางจอ
        bar = tk.Frame(self.canvas, bg="#1C1C24", bd=0)
        self.size_lbl = tk.Label(bar, text="", fg="#FFFFFF", bg="#1C1C24",
                                 font=(UI_FONT, 14, "bold"), padx=14)
        self.size_lbl.pack(side="left")
        tk.Button(bar, text="✓ ตกลง", command=self.confirm, bd=0,
                  bg="#7C5CFC", fg="#FFFFFF", activebackground="#6B4BEB",
                  activeforeground="#FFFFFF", font=(UI_FONT, 13, "bold"),
                  padx=16, pady=6, cursor="hand2").pack(side="left", padx=(6, 4), pady=6)
        tk.Button(bar, text="✕ ยกเลิก", command=self.cancel, bd=0,
                  bg="#33333F", fg="#FFFFFF", activebackground="#44444F",
                  activeforeground="#FFFFFF", font=(UI_FONT, 13),
                  padx=14, pady=6, cursor="hand2").pack(side="left", padx=(0, 8), pady=6)
        self.canvas.create_window(sw // 2, 30, window=bar, anchor="n")

        self.canvas.create_text(
            sw // 2, 78,
            text="ลากที่มุมหรือขอบเพื่อปรับขนาด • ลากกลางกรอบเพื่อย้าย • "
                 "ลากที่พื้นที่ว่างเพื่อวาดใหม่  (Enter = ตกลง, Esc = ยกเลิก)",
            fill="#B8B8C8", font=(UI_FONT, 13),
        )

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Double-Button-1>", lambda e: self.confirm())
        self.top.bind("<Escape>", lambda e: self.cancel())
        self.top.bind("<Return>", lambda e: self.confirm())
        self.top.bind("<Button-3>", lambda e: self.cancel())

        self.top.after(50, self.top.focus_force)
        self.redraw()

    # ---- ตำแหน่งจุดจับทั้ง 8 จุด ----
    def _handles(self):
        mx = (self.x0 + self.x1) // 2
        my = (self.y0 + self.y1) // 2
        return {
            "nw": (self.x0, self.y0), "n": (mx, self.y0), "ne": (self.x1, self.y0),
            "e": (self.x1, my), "se": (self.x1, self.y1), "s": (mx, self.y1),
            "sw": (self.x0, self.y1), "w": (self.x0, my),
        }

    def _hit(self, x, y):
        for name, (hx, hy) in self._handles().items():
            if abs(x - hx) <= self.HIT and abs(y - hy) <= self.HIT:
                return name
        if self.x0 < x < self.x1 and self.y0 < y < self.y1:
            return "move"
        return "new"

    _CURSORS = {
        "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
        "n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we",
        "move": "fleur", "new": "crosshair",
    }

    def on_hover(self, e):
        if self.mode:
            return
        try:
            self.canvas.config(cursor=self._CURSORS.get(self._hit(e.x, e.y), "crosshair"))
        except tk.TclError:
            self.canvas.config(cursor="crosshair")

    def on_press(self, e):
        self.mode = self._hit(e.x, e.y)
        if self.mode == "new":
            self.anchor = (e.x, e.y)
            self.x0 = self.x1 = e.x
            self.y0 = self.y1 = e.y
        self.last = (e.x, e.y)

    def on_drag(self, e):
        if not self.mode:
            return
        x = max(0, min(e.x, self.sw))
        y = max(0, min(e.y, self.sh))
        m = self.mode

        if m == "new":
            ax, ay = self.anchor
            self.x0, self.x1 = min(ax, x), max(ax, x)
            self.y0, self.y1 = min(ay, y), max(ay, y)
        elif m == "move":
            dx, dy = x - self.last[0], y - self.last[1]
            w, h = self.x1 - self.x0, self.y1 - self.y0
            self.x0 = max(0, min(self.x0 + dx, self.sw - w))
            self.y0 = max(0, min(self.y0 + dy, self.sh - h))
            self.x1, self.y1 = self.x0 + w, self.y0 + h
            self.last = (x, y)
        else:
            if "w" in m:
                self.x0 = min(x, self.x1 - self.MIN)
            if "e" in m:
                self.x1 = max(x, self.x0 + self.MIN)
            if "n" in m:
                self.y0 = min(y, self.y1 - self.MIN)
            if "s" in m:
                self.y1 = max(y, self.y0 + self.MIN)
        self.redraw()

    def on_release(self, e):
        self.mode = None

    # ---- วาดใหม่ ----
    def redraw(self):
        self.canvas.delete("sel")
        self.canvas.create_rectangle(
            self.x0, self.y0, self.x1, self.y1,
            outline=ACCENT, width=2, tags="sel",
        )
        h = self.HANDLE
        for hx, hy in self._handles().values():
            self.canvas.create_rectangle(
                hx - h, hy - h, hx + h, hy + h,
                fill="#FFFFFF", outline=ACCENT, width=2, tags="sel",
            )
        w = int(self.x1 - self.x0)
        ht = int(self.y1 - self.y0)
        self.size_lbl.config(text=f"{w} × {ht} px")

    def confirm(self):
        w = int(self.x1 - self.x0); h = int(self.y1 - self.y0)
        w -= w % 2; h -= h % 2
        x = int(self.x0); y = int(self.y0)
        self.top.destroy()
        if w >= 16 and h >= 16:
            self.callback(self.off_x + x, self.off_y + y, w, h)
        else:
            self.callback(None, None, None, None)

    def cancel(self):
        self.top.destroy()
        self.callback(None, None, None, None)


# ----------------------------------------------------------------------------
#  กรอบแสดงสถานะรอบพื้นที่บันทึก (แดง = พร้อม / เขียวกะพริบ = กำลังอัด)
# ----------------------------------------------------------------------------
class BorderOverlay:
    CHROMA = "#FF00FE"   # สีคีย์ทำให้พื้นที่ตรงกลางโปร่งใส + คลิกทะลุได้
    BW = 5               # ความหนาเส้นกรอบ
    RED = "#FF4D6D"
    GREEN = "#27E07A"
    GREEN_DIM = "#127C42"   # เขียวเข้มสำหรับจังหวะกะพริบ
    ORANGE = "#FFB020"

    def __init__(self, master):
        self.master = master
        self.color = self.RED
        self._blink_on = True
        self._job = None
        self._rect = None
        self._visible = False
        self.exclude_ok = False

        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        try:
            self.top.attributes("-transparentcolor", self.CHROMA)
        except tk.TclError:
            pass
        self.top.configure(bg=self.CHROMA)
        self.canvas = tk.Canvas(self.top, bg=self.CHROMA,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.top.withdraw()

    def place(self, region, screen_geom):
        """วางกรอบให้อยู่ 'นอก' พื้นที่บันทึกทั้งหมด (เป็นแถบ 4 ด้านในวงแหวนนอกพื้นที่อัด)
        โหมดเลือกพื้นที่: กรอบจะไม่ติดในวิดีโอ
        โหมดเต็มจอ: กรอบอยู่ขอบจอ (ผู้เรียกควรซ่อนตอนอัด)"""
        bw = self.BW
        if region:
            x, y, w, h = region
        else:
            x, y, w, h = screen_geom
            x += bw; y += bw; w -= bw * 2; h -= bw * 2
        gx, gy = x - bw, y - bw
        gw, gh = w + bw * 2, h + bw * 2
        self.top.geometry(f"{gw}x{gh}+{gx}+{gy}")
        self.canvas.delete("all")
        # แถบ 4 ด้าน เติมเต็มเฉพาะ 'วงแหวน' หนา bw รอบพื้นที่อัด (พื้นที่อัดอยู่ช่วง [bw, bw+w])
        self._bars = [
            self.canvas.create_rectangle(0, 0, gw, bw, width=0, fill=self.color),
            self.canvas.create_rectangle(0, gh - bw, gw, gh, width=0, fill=self.color),
            self.canvas.create_rectangle(0, 0, bw, gh, width=0, fill=self.color),
            self.canvas.create_rectangle(gw - bw, 0, gw, gh, width=0, fill=self.color),
        ]
        self.top.deiconify()
        self.top.lift()
        self.exclude_ok = exclude_from_capture(self.top)
        self._visible = True

    def _apply(self, color):
        self.color = color
        for b in getattr(self, "_bars", []):
            try:
                self.canvas.itemconfig(b, fill=color)
            except tk.TclError:
                pass

    def set_idle(self):
        self._stop_blink()
        self._apply(self.RED)

    def set_paused(self):
        self._stop_blink()
        self._apply(self.ORANGE)

    def set_recording(self):
        self._stop_blink()
        self._blink_on = True
        self._apply(self.GREEN)
        self._tick()

    def _tick(self):
        self._blink_on = not self._blink_on
        self._apply(self.GREEN if self._blink_on else self.GREEN_DIM)
        try:
            self._job = self.top.after(600, self._tick)
        except tk.TclError:
            self._job = None

    def _stop_blink(self):
        if self._job is not None:
            try:
                self.top.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def hide(self):
        self._stop_blink()
        try:
            self.top.withdraw()
        except tk.TclError:
            pass
        self._visible = False

    def destroy(self):
        self._stop_blink()
        try:
            self.top.destroy()
        except Exception:
            pass


# ----------------------------------------------------------------------------
#  ไอคอนแสดงสถานะเสียง (ลำโพง/ไมค์) ที่ลอยอยู่ด้านล่างจอ — ไม่ติดในวิดีโอ
# ----------------------------------------------------------------------------
class AudioIndicator:
    GREEN = "#27E07A"
    GREEN_DIM = "#127C42"
    FG = "#FFFFFF"
    BG = "#16161C"

    def __init__(self, master):
        self.master = master
        self._job = None
        self._on = True
        self._dot = None
        self.exclude_ok = False

        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        try:
            self.top.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        self.top.configure(bg=self.BG)
        self.canvas = tk.Canvas(self.top, bg=self.BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.top.withdraw()

    def _speaker(self, x, y, color):
        c = self.canvas
        c.create_rectangle(x, y + 6, x + 5, y + 14, fill=color, outline=color)
        c.create_polygon(x + 5, y + 6, x + 12, y, x + 12, y + 20, x + 5, y + 14,
                         fill=color, outline=color)
        c.create_arc(x + 11, y - 1, x + 23, y + 21, start=-55, extent=110,
                     style="arc", outline=color, width=2)
        c.create_arc(x + 14, y + 2, x + 28, y + 18, start=-55, extent=110,
                     style="arc", outline=color, width=2)

    def _mic(self, x, y, color):
        c = self.canvas
        c.create_oval(x + 3, y, x + 13, y + 16, fill=color, outline=color)
        c.create_arc(x, y + 8, x + 16, y + 22, start=200, extent=140,
                     style="arc", outline=color, width=2)
        c.create_line(x + 8, y + 19, x + 8, y + 24, fill=color, width=2)
        c.create_line(x + 3, y + 24, x + 13, y + 24, fill=color, width=2)

    def show(self, sys_on, mic_on, screen_geom):
        import tkinter.font as tkfont
        if not (sys_on or mic_on):
            self.hide()
            return
        font = tkfont.Font(family=UI_FONT, size=13, weight="bold")
        H = 46
        pad = 16
        gap = 10
        x = pad
        # คำนวณความกว้างก่อน
        width = pad + 16 + gap  # จุดสถานะ
        layout = []  # (kind, label, icon_w, text_w)
        if sys_on:
            tw = font.measure("เสียงระบบ")
            layout.append(("spk", "เสียงระบบ", 30, tw)); width += 30 + 6 + tw + gap
        if mic_on:
            tw = font.measure("ไมค์")
            layout.append(("mic", "ไมค์", 18, tw)); width += 18 + 6 + tw + gap
        width += pad - gap

        sw = self.top.winfo_screenwidth()
        sh = self.top.winfo_screenheight()
        gx = (sw - width) // 2
        gy = sh - H - 60
        self.top.geometry(f"{width}x{H}+{gx}+{gy}")

        c = self.canvas
        c.delete("all")
        cy = H // 2
        # จุดสถานะกะพริบ
        self._dot = c.create_oval(x, cy - 6, x + 12, cy + 6,
                                  fill=self.GREEN, outline=self.GREEN)
        x += 16 + gap
        for kind, label, iw, tw in layout:
            if kind == "spk":
                self._speaker(x, cy - 10, self.FG)
            else:
                self._mic(x, cy - 12, self.FG)
            x += iw + 6
            c.create_text(x, cy, text=label, anchor="w",
                          fill=self.FG, font=(UI_FONT, 13, "bold"))
            x += tw + gap

        self.top.deiconify()
        self.top.lift()
        self.exclude_ok = exclude_from_capture(self.top)
        self._on = True
        self._blink()

    def _blink(self):
        self._stop_blink()
        self._on = not self._on
        try:
            self.canvas.itemconfig(self._dot,
                                   fill=self.GREEN if self._on else self.GREEN_DIM,
                                   outline=self.GREEN if self._on else self.GREEN_DIM)
            self._job = self.top.after(600, self._blink)
        except tk.TclError:
            self._job = None

    def _stop_blink(self):
        if self._job is not None:
            try:
                self.top.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def hide(self):
        self._stop_blink()
        try:
            self.top.withdraw()
        except tk.TclError:
            pass

    def destroy(self):
        self._stop_blink()
        try:
            self.top.destroy()
        except Exception:
            pass


# ----------------------------------------------------------------------------
#  ตัวควบคุมการบันทึก
# ----------------------------------------------------------------------------
class WavRecorder:
    """อัดเสียงจากอุปกรณ์ WASAPI (เสียงระบบ/ไมโครโฟน) ลงไฟล์ WAV
    ** เปิด/อ่าน/ปิด PortAudio ในเธรดเดียวกันทั้งหมด เพื่อกันแครช **"""

    def __init__(self, dev, path):
        self.dev = dev
        self.path = path
        self.running = False
        self.thread = None
        self._ready = threading.Event()
        self.t_first = None     # เวลา (monotonic) ที่ได้เสียงก้อนแรก

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._ready.wait(timeout=2)

    def _run(self):
        import wave
        import pyaudiowpatch as pyaudio
        p = stream = wf = None
        try:
            ch = max(1, int(self.dev.get("channels", 2)))
            rate = int(self.dev.get("rate", 48000))
            p = pyaudio.PyAudio()
            wf = wave.open(self.path, "wb")
            wf.setnchannels(ch); wf.setsampwidth(2); wf.setframerate(rate)
            stream = p.open(
                format=pyaudio.paInt16, channels=ch, rate=rate,
                frames_per_buffer=1024, input=True,
                input_device_index=int(self.dev["index"]),
            )
            self._ready.set()
            while self.running:
                try:
                    data = stream.read(1024, exception_on_overflow=False)
                    if self.t_first is None:
                        self.t_first = time.monotonic()
                    wf.writeframes(data)
                except Exception:
                    break
        except Exception:
            pass
        finally:
            self._ready.set()
            for fn in (lambda: stream and stream.stop_stream(),
                       lambda: stream and stream.close(),
                       lambda: wf and wf.close(),
                       lambda: p and p.terminate()):
                try:
                    fn()
                except Exception:
                    pass

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)


def list_audio_devices():
    """คืน (loopbacks, mics) แต่ละตัวเป็น dict {index,name,rate,channels}"""
    if not HAS_PYAUDIO:
        return [], []
    import pyaudiowpatch as pyaudio
    loopbacks, mics = [], []
    p = pyaudio.PyAudio()
    try:
        loop_idx = set()
        try:
            for info in p.get_loopback_device_info_generator():
                loop_idx.add(info["index"])
                loopbacks.append({
                    "index": info["index"],
                    "name": info["name"].replace(" [Loopback]", "") + " (เสียงลำโพง)",
                    "rate": int(info["defaultSampleRate"]),
                    "channels": min(int(info.get("maxInputChannels", 2)) or 2, 2),
                })
        except Exception:
            pass
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
            except Exception:
                continue
            if info.get("maxInputChannels", 0) > 0 and i not in loop_idx:
                if info.get("isLoopbackDevice", False):
                    continue
                mics.append({
                    "index": i,
                    "name": info["name"],
                    "rate": int(info["defaultSampleRate"]),
                    "channels": min(int(info["maxInputChannels"]), 2) or 1,
                })
    finally:
        p.terminate()
    return loopbacks, mics


class Recorder:
    """อัดวิดีโอด้วย FFmpeg + อัดเสียงด้วย WASAPI แล้วรวมเป็นไฟล์เดียว
    วัดออฟเซ็ตเริ่มต้นอัตโนมัติ + ให้ผู้ใช้จูนเพิ่มได้ (sync_ms)"""

    def __init__(self, ffmpeg):
        self.ffmpeg = ffmpeg
        self.proc = None
        self.clips = []
        self.idx = 0
        self.tmp_dir = None
        self.settings = None
        self.is_active = False
        self._audio = []

    def build_video_cmd(self, out_path):
        s = self.settings
        cmd = [self.ffmpeg, "-y"]
        if IS_WINDOWS:
            cmd += ["-f", "gdigrab", "-framerate", str(s["fps"]),
                    "-thread_queue_size", "1024"]
            if s["region"]:
                x, y, w, h = s["region"]
                cmd += ["-offset_x", str(x), "-offset_y", str(y),
                        "-video_size", f"{w}x{h}"]
            cmd += ["-i", "desktop"]
        elif IS_LINUX:
            disp = os.environ.get("DISPLAY", ":0.0")
            cmd += ["-f", "x11grab", "-framerate", str(s["fps"])]
            if s["region"]:
                x, y, w, h = s["region"]
                cmd += ["-video_size", f"{w}x{h}", "-i", f"{disp}+{x},{y}"]
            else:
                cmd += ["-i", disp]
        else:
            cmd += ["-f", "avfoundation", "-framerate", str(s["fps"]), "-i", "1:none"]

        if s["scale"]:
            tw, th = s["scale"]
            cmd += ["-vf", f"scale={tw}:{th}:flags=lanczos"]

        enc = s["encoder"]
        if enc == "nvenc":
            cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                    "-cq", str(s["cq"]), "-b:v", s["bitrate"]]
        elif enc == "amf":
            cmd += ["-c:v", "h264_amf", "-quality", "quality", "-b:v", s["bitrate"]]
        elif enc == "qsv":
            cmd += ["-c:v", "h264_qsv", "-global_quality", str(s["cq"]),
                    "-b:v", s["bitrate"]]
        else:
            cmd += ["-c:v", "libx264", "-preset", s["x264_preset"], "-crf", str(s["crf"])]
        cmd += ["-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", out_path]
        return cmd

    def _log(self):
        return open(os.path.join(self.tmp_dir, "ffmpeg.log"), "a", encoding="utf-8")

    def _start_clip(self):
        clip = {"video": os.path.join(self.tmp_dir, f"v_{self.idx:03d}.mp4"),
                "sys": None, "mic": None, "t_video": None, "recs": [], "reader": None}
        cmd = self.build_video_cmd(clip["video"])
        log = self._log()
        log.write("\n\n=== " + " ".join(cmd) + "\n")
        log.flush()
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW,
        )

        def _reader(proc, clip, log):
            tail = b""
            try:
                while True:
                    chunk = proc.stderr.read(512)
                    if not chunk:
                        break
                    if clip.get("t_video") is None and b"frame=" in (tail + chunk):
                        clip["t_video"] = time.monotonic()
                    tail = chunk[-16:]
                    try:
                        log.write(chunk.decode("utf-8", "ignore"))
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                try:
                    log.close()
                except Exception:
                    pass
        t = threading.Thread(target=_reader, args=(self.proc, clip, log), daemon=True)
        t.start()
        clip["reader"] = t

        self._audio = []
        s = self.settings
        if HAS_PYAUDIO:
            if s.get("sys_dev"):
                clip["sys"] = os.path.join(self.tmp_dir, f"sys_{self.idx:03d}.wav")
                try:
                    rec = WavRecorder(s["sys_dev"], clip["sys"]); rec.start()
                    self._audio.append(rec); clip["recs"].append(rec)
                except Exception:
                    clip["sys"] = None
            if s.get("mic_dev"):
                clip["mic"] = os.path.join(self.tmp_dir, f"mic_{self.idx:03d}.wav")
                try:
                    rec = WavRecorder(s["mic_dev"], clip["mic"]); rec.start()
                    self._audio.append(rec); clip["recs"].append(rec)
                except Exception:
                    clip["mic"] = None
        self.clips.append(clip)

    def start(self, settings):
        self.settings = settings
        try:
            for name in os.listdir(settings["out_dir"]):
                if name.startswith(".tmp_rec_"):
                    shutil.rmtree(os.path.join(settings["out_dir"], name),
                                  ignore_errors=True)
        except Exception:
            pass
        self.tmp_dir = os.path.join(
            settings["out_dir"], f".tmp_rec_{datetime.now():%Y%m%d_%H%M%S}")
        os.makedirs(self.tmp_dir, exist_ok=True)
        self.clips = []
        self.idx = 0
        self.is_active = True
        self._start_clip()

    def _stop_clip(self):
        for rec in self._audio:
            try:
                rec.stop()
            except Exception:
                pass
        self._audio = []
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q"); self.proc.stdin.flush()
            except Exception:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        self.proc = None

    def pause(self):
        self._stop_clip()

    def resume(self):
        self.idx += 1
        self._start_clip()

    def _mux_clip(self, clip, out_path):
        v = clip["video"]
        auds = [a for a in (clip.get("sys"), clip.get("mic"))
                if a and os.path.exists(a) and os.path.getsize(a) > 1024]
        if not auds:
            shutil.copy(v, out_path)
            return

        # ซิงก์: วัดออฟเซ็ตอัตโนมัติ (เฟรมแรกภาพ - เสียงก้อนแรก) + ค่าปรับมือ
        ss_audio = ss_video = 0.0
        t_video = clip.get("t_video")
        ts = [r.t_first for r in clip.get("recs", []) if r.t_first]
        t_audio = min(ts) if ts else None
        auto = (t_video - t_audio) if (t_video and t_audio) else 0.0
        d = auto + self.settings.get("sync_ms", 0) / 1000.0
        if d > 0.01:
            ss_audio = round(min(d, 10.0), 3)
        elif d < -0.01:
            ss_video = round(min(-d, 10.0), 3)

        cmd = [self.ffmpeg, "-y"]
        if ss_video:
            cmd += ["-ss", f"{ss_video:.3f}"]
        cmd += ["-i", v]
        for a in auds:
            if ss_audio:
                cmd += ["-ss", f"{ss_audio:.3f}"]
            cmd += ["-i", a]
        if len(auds) == 1:
            cmd += ["-map", "0:v", "-map", "1:a"]
        else:
            mix = "".join(f"[{i+1}:a]" for i in range(len(auds)))
            mix += f"amix=inputs={len(auds)}:duration=longest:normalize=0[a]"
            cmd += ["-filter_complex", mix, "-map", "0:v", "-map", "[a]"]
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-shortest", "-movflags", "+faststart", out_path]
        logf = self._log()
        try:
            logf.write("\n\n=== MUX " + " ".join(cmd) + "\n")
            subprocess.run(cmd, stdout=logf, stderr=logf,
                           creationflags=CREATE_NO_WINDOW)
        finally:
            try:
                logf.close()
            except Exception:
                pass

    def stop(self, final_path):
        self.is_active = False
        self._stop_clip()
        for c in self.clips:
            th = c.get("reader")
            if th:
                try:
                    th.join(timeout=3)
                except Exception:
                    pass

        muxed = []
        for i, clip in enumerate(self.clips):
            if not (os.path.exists(clip["video"]) and os.path.getsize(clip["video"]) > 0):
                continue
            out = os.path.join(self.tmp_dir, f"m_{i:03d}.mp4")
            try:
                self._mux_clip(clip, out)
            except Exception:
                out = clip["video"]
            if os.path.exists(out) and os.path.getsize(out) > 0:
                muxed.append(out)

        if not muxed:
            self._cleanup()
            return False

        if len(muxed) == 1:
            shutil.move(muxed[0], final_path)
        else:
            list_file = os.path.join(self.tmp_dir, "concat.txt")
            with open(list_file, "w", encoding="utf-8") as f:
                for p in muxed:
                    f.write(f"file '{os.path.abspath(p)}'\n")
            subprocess.run(
                [self.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                 "-c", "copy", "-movflags", "+faststart", final_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        ok = os.path.exists(final_path)
        self._cleanup()
        return ok

    def _cleanup(self):
        if self.tmp_dir and os.path.isdir(self.tmp_dir):
            for _ in range(6):
                shutil.rmtree(self.tmp_dir, ignore_errors=True)
                if not os.path.isdir(self.tmp_dir):
                    break
                time.sleep(0.3)
        self.tmp_dir = None


# ----------------------------------------------------------------------------
#  แอปหลัก (UI)
# ----------------------------------------------------------------------------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # เลือกฟอนต์ไทยที่คมชัดที่สุด (ต้องทำหลังสร้าง root)
        global UI_FONT
        UI_FONT = pick_thai_font()

        ctk.set_appearance_mode("dark")
        self.title(APP_NAME)
        self.geometry("560x760")
        self.minsize(520, 700)
        self.configure(fg_color=BG_DEEP)

        self.ffmpeg = find_ffmpeg()
        self.recorder = Recorder(self.ffmpeg) if self.ffmpeg else None
        self.region = None              # (x, y, w, h)  None = เต็มจอ
        self.rec_state = "idle"             # idle | recording | paused
        self.elapsed = 0.0
        self._seg_start = None
        self.loopbacks, self.mics = list_audio_devices()
        self.sys_map = {d["name"]: d for d in self.loopbacks}
        self.mic_map = {d["name"]: d for d in self.mics}
        self._config = load_config()

        self._build_ui()
        self._apply_config()    # โหลดค่าที่จำไว้ทับค่าเริ่มต้น

        # กรอบแสดงสถานะรอบพื้นที่บันทึก
        self.border = BorderOverlay(self)
        self.audio_ind = AudioIndicator(self)   # ไอคอนเสียง/ไมค์ ด้านล่างจอ
        self._audio_flags = (False, False)
        self.after(400, self._update_border)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._refresh_status_loop()

        if not self.ffmpeg:
            self.after(300, self._warn_no_ffmpeg)

    # ---------- การสร้าง UI ----------
    def _build_ui(self):
        # หัวข้อ
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(22, 6))
        ctk.CTkLabel(
            header, text="●", text_color=ACCENT, font=(UI_FONT, 30, "bold")
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=f"  {APP_NAME}",
            font=(UI_FONT, 27, "bold"), text_color="#FFFFFF",
        ).pack(side="left")
        ctk.CTkLabel(
            self, text="บันทึกหน้าจอคมชัดระดับ 4K  •  เสียง + ไมค์  •  ไม่จำกัดเวลา",
            font=(UI_FONT, 14), text_color=TXT_DIM,
        ).pack(anchor="w", padx=26)

        # ตัวจับเวลา + สถานะ
        timer_card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=18)
        timer_card.pack(fill="x", padx=20, pady=16)
        self.timer_lbl = ctk.CTkLabel(
            timer_card, text="00:00:00",
            font=("Consolas", 44, "bold"), text_color="#FFFFFF",
        )
        self.timer_lbl.pack(pady=(18, 2))
        self.status_lbl = ctk.CTkLabel(
            timer_card, text="พร้อมบันทึก", font=(UI_FONT, 15), text_color=TXT_DIM,
        )
        self.status_lbl.pack(pady=(0, 16))

        # การ์ดตั้งค่า
        card = ctk.CTkScrollableFrame(self, fg_color=BG_CARD, corner_radius=18)
        card.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # -- พื้นที่บันทึก --
        self._section(card, "พื้นที่บันทึก")
        self.area_seg = ctk.CTkSegmentedButton(
            card, values=["เต็มจอ", "เลือกพื้นที่"],
            command=self._on_area_change,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            font=(UI_FONT, 15),
        )
        self.area_seg.set("เต็มจอ")
        self.area_seg.pack(fill="x", pady=(0, 6))
        self.region_lbl = ctk.CTkLabel(
            card, text="กำลังจับภาพ: ทั้งหน้าจอ", font=(UI_FONT, 14), text_color=TXT_DIM
        )
        self.region_lbl.pack(anchor="w", pady=(0, 4))
        self.border_switch = ctk.CTkSwitch(
            card, text="แสดงกรอบสถานะ (แดง = พร้อม / เขียวกะพริบ = กำลังอัด)",
            progress_color=ACCENT, font=(UI_FONT, 13),
            command=self._toggle_border,
        )
        self.border_switch.select()
        self.border_switch.pack(anchor="w", pady=(0, 12))

        # -- ความละเอียด / คุณภาพ --
        self._section(card, "ความคมชัด")
        self.res_menu = ctk.CTkOptionMenu(
            card,
            values=["ต้นฉบับ (Native)", "4K — 3840×2160", "2K — 2560×1440",
                    "1080p — 1920×1080", "720p — 1280×720"],
            fg_color="#26262F", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            font=(UI_FONT, 15),
        )
        self.res_menu.set("ต้นฉบับ (Native)")
        self.res_menu.pack(fill="x", pady=(0, 8))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row, text="เฟรมเรต", font=(UI_FONT, 15)).pack(side="left")
        self.fps_menu = ctk.CTkOptionMenu(
            row, values=["30 fps", "60 fps", "120 fps"], width=120,
            fg_color="#26262F", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        )
        self.fps_menu.set("60 fps")
        self.fps_menu.pack(side="right")

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row2, text="คุณภาพ", font=(UI_FONT, 15)).pack(side="left")
        self.q_menu = ctk.CTkOptionMenu(
            row2, values=["สูงมาก (ไฟล์ใหญ่)", "สูง", "ปานกลาง"], width=160,
            fg_color="#26262F", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        )
        self.q_menu.set("สูง")
        self.q_menu.pack(side="right")

        row3 = ctk.CTkFrame(card, fg_color="transparent")
        row3.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(row3, text="ตัวเข้ารหัส", font=(UI_FONT, 15)).pack(side="left")
        self.enc_menu = ctk.CTkOptionMenu(
            row3,
            values=["อัตโนมัติ (CPU)", "NVIDIA GPU (NVENC)",
                    "AMD GPU (AMF)", "Intel (QSV)"],
            width=190,
            fg_color="#26262F", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        )
        self.enc_menu.set("อัตโนมัติ (CPU)")
        self.enc_menu.pack(side="right")

        # -- เสียง --
        self._section(card, "เสียง")

        sys_row = ctk.CTkFrame(card, fg_color="transparent")
        sys_row.pack(fill="x")
        self.sys_switch = ctk.CTkSwitch(
            sys_row, text="บันทึกเสียงระบบ (เสียงในคอม)",
            progress_color=ACCENT, font=(UI_FONT, 15),
            command=self._toggle_audio_menus,
        )
        self.sys_switch.pack(side="left", anchor="w", pady=(0, 6))
        ctk.CTkButton(
            sys_row, text="?", width=30, command=self._help_system_audio,
            fg_color="#26262F", hover_color="#33333F", font=(UI_FONT, 14, "bold"),
        ).pack(side="right")
        self.sys_menu = ctk.CTkOptionMenu(
            card, values=self._sys_audio_options(),
            fg_color="#26262F", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            font=(UI_FONT, 14),
        )
        self.sys_menu.pack(fill="x", pady=(0, 10))

        self.mic_switch = ctk.CTkSwitch(
            card, text="บันทึกไมโครโฟน", progress_color=ACCENT, font=(UI_FONT, 15),
            command=self._toggle_audio_menus,
        )
        self.mic_switch.pack(anchor="w", pady=(0, 6))
        self.mic_menu = ctk.CTkOptionMenu(
            card, values=self._mic_options(),
            fg_color="#26262F", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            font=(UI_FONT, 14),
        )
        self.mic_menu.pack(fill="x", pady=(0, 6))

        ctk.CTkButton(
            card, text="🔄 ตรวจหาอุปกรณ์เสียงใหม่", command=self._refresh_audio,
            fg_color="#26262F", hover_color="#33333F", font=(UI_FONT, 13),
            height=32,
        ).pack(fill="x", pady=(0, 10))

        # -- ปรับซิงก์เสียง --
        self._sync_ms = 0   # ค่าจริง (แม่นระดับ ms) ใช้ตอนรวมไฟล์
        sync_row = ctk.CTkFrame(card, fg_color="transparent")
        sync_row.pack(fill="x")
        ctk.CTkLabel(sync_row, text="ปรับซิงก์เสียง", font=(UI_FONT, 14)).pack(side="left")
        ctk.CTkButton(
            sync_row, text="−1 เฟรม", width=64, command=lambda: self._nudge_sync(-1),
            fg_color="#26262F", hover_color="#33333F", font=(UI_FONT, 12),
        ).pack(side="left", padx=(10, 4))
        ctk.CTkButton(
            sync_row, text="+1 เฟรม", width=64, command=lambda: self._nudge_sync(1),
            fg_color="#26262F", hover_color="#33333F", font=(UI_FONT, 12),
        ).pack(side="left")
        ctk.CTkButton(
            sync_row, text="รีเซ็ต", width=52, command=self._reset_sync,
            fg_color="#26262F", hover_color="#33333F", font=(UI_FONT, 12),
        ).pack(side="left", padx=(4, 0))
        self.sync_val_lbl = ctk.CTkLabel(
            sync_row, text="0 ms", font=(UI_FONT, 14), text_color=ACCENT)
        self.sync_val_lbl.pack(side="right")
        self.sync_slider = ctk.CTkSlider(
            card, from_=-3000, to=3000, number_of_steps=600,
            command=self._on_sync_change,
            progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        )
        self.sync_slider.set(0)
        self.sync_slider.pack(fill="x", pady=(2, 2))
        ctk.CTkLabel(
            card, text="ปรับซิงก์อัตโนมัติทุกครั้งอยู่แล้ว • ใช้ปุ่ม/สไลเดอร์จูนเพิ่มถ้ายังเหลื่อมเล็กน้อย",
            font=(UI_FONT, 11), text_color=TXT_DIM,
        ).pack(anchor="w", pady=(0, 12))

        # ค่าเริ่มต้น: เลือกอุปกรณ์ที่ดีที่สุดให้อัตโนมัติ + เปิดเสียงระบบถ้าตรวจเจอ
        self._preselect_audio()
        self._toggle_audio_menus()

        # -- ที่บันทึกไฟล์ --
        self._section(card, "บันทึกไฟล์ไปที่")
        out_row = ctk.CTkFrame(card, fg_color="transparent")
        out_row.pack(fill="x", pady=(0, 6))
        default_dir = os.path.join(os.path.expanduser("~"), "Videos")
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~")
        self.out_dir = default_dir
        self.out_entry = ctk.CTkEntry(out_row, font=(UI_FONT, 14))
        self.out_entry.insert(0, self.out_dir)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            out_row, text="เลือก…", width=70, command=self._choose_dir,
            fg_color="#26262F", hover_color="#33333F",
        ).pack(side="right")

        # ปุ่มควบคุมหลัก
        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=(0, 18))
        self.rec_btn = ctk.CTkButton(
            ctrl, text="●  เริ่มบันทึก", height=54, corner_radius=14,
            font=(UI_FONT, 19, "bold"),
            fg_color=DANGER, hover_color=DANGER_HOVER,
            command=self._on_rec_btn,
        )
        self.rec_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.pause_btn = ctk.CTkButton(
            ctrl, text="⏸", width=70, height=54, corner_radius=14,
            font=(UI_FONT, 22), state="disabled",
            fg_color="#26262F", hover_color="#33333F",
            command=self._on_pause_btn,
        )
        self.pause_btn.pack(side="right")

    def _section(self, parent, text):
        ctk.CTkLabel(
            parent, text=text.upper(), font=(UI_FONT, 13, "bold"),
            text_color=ACCENT, anchor="w",
        ).pack(fill="x", pady=(10, 6))

    LOOPBACK_KW = ["stereo mix", "สเตอริโอ", "mix", "loopback", "what u hear",
                   "wave out", "virtual", "cable", "voicemeeter", "vb-audio"]
    MIC_KW = ["mic", "ไมโคร", "input", "line in", "array"]

    def _is_loopback(self, name):
        n = name.lower()
        return any(k in n for k in self.LOOPBACK_KW)

    def _is_mic(self, name):
        n = name.lower()
        return any(k in n for k in self.MIC_KW)

    def _sys_audio_options(self):
        if not HAS_PYAUDIO:
            return ["(ต้องติดตั้ง pyaudiowpatch ก่อน)"]
        if not self.loopbacks:
            return ["(ไม่พบเสียงระบบ — ลองกดตรวจหาใหม่)"]
        return [d["name"] for d in self.loopbacks]

    def _mic_options(self):
        if not HAS_PYAUDIO:
            return ["(ต้องติดตั้ง pyaudiowpatch ก่อน)"]
        if not self.mics:
            return ["(ไม่พบไมโครโฟน)"]
        return [d["name"] for d in self.mics]

    def _preselect_audio(self):
        """เลือกอุปกรณ์ที่เหมาะสมให้อัตโนมัติ และเปิดเสียงระบบถ้าตรวจเจอ"""
        if self.loopbacks:
            self.sys_menu.set(self.loopbacks[0]["name"])
            self.sys_switch.select()       # เปิดเสียงระบบเป็นค่าเริ่มต้น
        if self.mics:
            self.mic_menu.set(self.mics[0]["name"])

    # ---------- จำค่าตั้งค่า ----------
    def _apply_config(self):
        c = getattr(self, "_config", None)
        if not c:
            return
        try:
            if c.get("res"):
                self.res_menu.set(c["res"])
            if c.get("fps"):
                self.fps_menu.set(c["fps"])
            if c.get("quality"):
                self.q_menu.set(c["quality"])
            if c.get("encoder"):
                self.enc_menu.set(c["encoder"])

            # อุปกรณ์เสียง — ตั้งเฉพาะถ้ายังมีอุปกรณ์นั้นอยู่
            if c.get("sys_dev") in self.sys_map:
                self.sys_menu.set(c["sys_dev"])
            if c.get("mic_dev") in self.mic_map:
                self.mic_menu.set(c["mic_dev"])
            (self.sys_switch.select if c.get("sys_on") else self.sys_switch.deselect)()
            if c.get("mic_on") and self.mics:
                self.mic_switch.select()
            else:
                self.mic_switch.deselect()
            self._toggle_audio_menus()

            (self.border_switch.select if c.get("border", True)
             else self.border_switch.deselect)()

            if c.get("sync_ms") is not None:
                self._sync_ms = int(c["sync_ms"])
                self.sync_slider.set(self._sync_ms)
                self.sync_val_lbl.configure(text=f"{self._sync_ms} ms")

            od = c.get("out_dir")
            if od:
                self.out_dir = od
                self.out_entry.delete(0, "end")
                self.out_entry.insert(0, od)

            if c.get("area") == "region" and c.get("region"):
                self.region = tuple(c["region"])
                self.area_seg.set("เลือกพื้นที่")
                x, y, w, h = self.region
                self.region_lbl.configure(
                    text=f"กำลังจับภาพ: พื้นที่ {w}×{h} ที่ตำแหน่ง ({x}, {y})")
        except Exception:
            pass

    def _save_settings(self):
        try:
            cfg = {
                "res": self.res_menu.get(),
                "fps": self.fps_menu.get(),
                "quality": self.q_menu.get(),
                "encoder": self.enc_menu.get(),
                "sys_on": bool(self.sys_switch.get()),
                "sys_dev": self.sys_menu.get(),
                "mic_on": bool(self.mic_switch.get()),
                "mic_dev": self.mic_menu.get(),
                "border": bool(self.border_switch.get()),
                "sync_ms": int(self._sync_ms),
                "out_dir": self.out_entry.get().strip().strip('"') or self.out_dir,
                "area": "region" if self.region else "full",
                "region": list(self.region) if self.region else None,
            }
            save_config(cfg)
        except Exception:
            pass

    def _refresh_audio(self):
        self.loopbacks, self.mics = list_audio_devices()
        self.sys_map = {d["name"]: d for d in self.loopbacks}
        self.mic_map = {d["name"]: d for d in self.mics}
        self.sys_menu.configure(values=self._sys_audio_options())
        self.mic_menu.configure(values=self._mic_options())
        self._preselect_audio()
        self._toggle_audio_menus()
        if not HAS_PYAUDIO:
            messagebox.showwarning(
                "ยังอัดเสียงไม่ได้",
                "ยังไม่ได้ติดตั้งไลบรารีอัดเสียง\n\n"
                "เปิด PowerShell แล้วพิมพ์:\n"
                "  pip install pyaudiowpatch\n\n"
                "แล้วเปิดโปรแกรมใหม่อีกครั้ง",
            )
            return
        messagebox.showinfo(
            "ตรวจหาอุปกรณ์เสียง",
            f"เสียงระบบ (ลำโพง): {len(self.loopbacks)} รายการ\n"
            f"ไมโครโฟน: {len(self.mics)} รายการ",
        )

    def _help_system_audio(self):
        messagebox.showinfo(
            "เกี่ยวกับการอัดเสียง",
            "โปรแกรมนี้อัด \"เสียงที่ออกลำโพง\" ได้โดยตรง (WASAPI Loopback)\n"
            "ไม่ต้องเปิด Stereo Mix หรือติดตั้งอะไรเพิ่ม\n\n"
            "• เสียงระบบ = เสียงทุกอย่างที่ดังในคอม (เช่น เสียง YouTube)\n"
            "• ไมโครโฟน = เสียงพูดของเรา\n"
            "• เปิดทั้งสองพร้อมกันได้ โปรแกรมจะผสมเสียงให้\n\n"
            "ถ้าเมนูว่าง ให้กด \"🔄 ตรวจหาอุปกรณ์เสียงใหม่\"\n"
            "ถ้าขึ้นว่าต้องติดตั้ง pyaudiowpatch ให้พิมพ์ใน PowerShell:\n"
            "  pip install pyaudiowpatch",
        )

    # ---------- กรอบแสดงสถานะ ----------
    def _update_border(self):
        """วางกรอบรอบพื้นที่บันทึกปัจจุบัน แล้วตั้งสีตามสถานะ"""
        if not hasattr(self, "border"):
            return
        if not self.border_switch.get():
            self.border.hide()
            return
        screen = get_screen_geometry()
        self.border.place(self.region, screen)
        # โหมดเต็มจอ + กำลังอัด + เครื่องกันแคปไม่ได้ -> ซ่อน ไม่งั้นกรอบจะติดในวิดีโอ
        if (self.region is None and self.rec_state in ("recording", "paused")
                and not self.border.exclude_ok):
            self.border.hide()
            return
        if self.rec_state == "recording":
            self.border.set_recording()
        elif self.rec_state == "paused":
            self.border.set_paused()
        else:
            self.border.set_idle()

    def _show_audio_indicator(self):
        sys_on, mic_on = self._audio_flags
        if not (sys_on or mic_on):
            self.audio_ind.hide()
            return
        self.audio_ind.show(sys_on, mic_on, get_screen_geometry())

    def _toggle_border(self):
        self._update_border()

    def _on_close(self):
        self._save_settings()
        try:
            if self.rec_state in ("recording", "paused"):
                self.recorder.stop(os.path.join(
                    self.out_dir, f"บันทึก_{datetime.now():%Y%m%d_%H%M%S}.mp4"))
        except Exception:
            pass
        if hasattr(self, "border"):
            self.border.destroy()
        if hasattr(self, "audio_ind"):
            self.audio_ind.destroy()
        self.destroy()

    # ---------- เหตุการณ์ UI ----------
    def _on_area_change(self, value):
        if value == "เลือกพื้นที่":
            self.area_seg.configure(state="disabled")
            if hasattr(self, "border"):
                self.border.hide()
            self.withdraw()
            self.after(250, self._open_region_selector)
        else:
            self.region = None
            self.region_lbl.configure(text="กำลังจับภาพ: ทั้งหน้าจอ")
            self._update_border()

    def _open_region_selector(self):
        # ส่งกรอบเดิมเข้าไปด้วย เพื่อให้แก้ไข/ปรับมุมต่อจากของเดิมได้
        RegionSelector(self, self._region_done, initial=self.region)

    def _region_done(self, x, y, w, h):
        """เรียกเมื่อปิดหน้าต่างเลือกพื้นที่ (ทั้งกรณีตกลงและยกเลิก)"""
        self.deiconify()
        self.area_seg.configure(state="normal")
        if x is None:
            # ยกเลิก หรือกรอบเล็กเกินไป — คงค่าเดิมไว้
            if not self.region:
                self.area_seg.set("เต็มจอ")
                self.region_lbl.configure(text="กำลังจับภาพ: ทั้งหน้าจอ")
        else:
            self.region = (x, y, w, h)
            self.area_seg.set("เลือกพื้นที่")
            self.region_lbl.configure(
                text=f"กำลังจับภาพ: พื้นที่ {w}×{h} ที่ตำแหน่ง ({x}, {y})"
            )
        self.after(150, self._update_border)

    def _toggle_audio_menus(self):
        self.sys_menu.configure(state="normal" if self.sys_switch.get() else "disabled")
        self.mic_menu.configure(state="normal" if self.mic_switch.get() else "disabled")

    def _on_sync_change(self, value):
        self._sync_ms = int(round(value))
        self.sync_val_lbl.configure(text=f"{self._sync_ms} ms")

    def _nudge_sync(self, direction):
        """ปรับซิงก์ทีละ 1 เฟรม ตามเฟรมเรตที่เลือก (ละเอียดกว่าสไลเดอร์)"""
        try:
            fps = int(self.fps_menu.get().split()[0])
        except Exception:
            fps = 60
        frame_ms = max(1, round(1000 / fps))
        self._sync_ms = max(-3000, min(3000, self._sync_ms + direction * frame_ms))
        self.sync_slider.set(self._sync_ms)   # ขยับสไลเดอร์ตาม (ไม่ trigger callback)
        self.sync_val_lbl.configure(text=f"{self._sync_ms} ms")

    def _reset_sync(self):
        self._sync_ms = 0
        self.sync_slider.set(0)
        self.sync_val_lbl.configure(text="0 ms")

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir)
        if d:
            self.out_dir = d
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, d)

    def _warn_no_ffmpeg(self):
        messagebox.showwarning(
            "ไม่พบ FFmpeg",
            "โปรแกรมต้องใช้ FFmpeg ในการบันทึก\n\n"
            "วิธีติดตั้งบน Windows (เลือกอย่างใดอย่างหนึ่ง):\n"
            "  • เปิด PowerShell แล้วพิมพ์:  winget install ffmpeg\n"
            "  • หรือ:  choco install ffmpeg\n"
            "  • หรือดาวน์โหลดจาก ffmpeg.org แล้ววางไฟล์ ffmpeg.exe\n"
            "    ไว้โฟลเดอร์เดียวกับโปรแกรมนี้\n\n"
            "ติดตั้งเสร็จแล้วเปิดโปรแกรมใหม่อีกครั้ง",
        )

    # ---------- ตรรกะการบันทึก ----------
    def _collect_settings(self):
        # ความละเอียด
        scale = None
        res = self.res_menu.get()
        res_map = {
            "4K — 3840×2160": (3840, 2160),
            "2K — 2560×1440": (2560, 1440),
            "1080p — 1920×1080": (1920, 1080),
            "720p — 1280×720": (1280, 720),
        }
        if res in res_map:
            scale = res_map[res]

        fps = int(self.fps_menu.get().split()[0])

        q = self.q_menu.get()
        crf = {"สูงมาก (ไฟล์ใหญ่)": 16, "สูง": 20, "ปานกลาง": 24}[q]
        cq = {"สูงมาก (ไฟล์ใหญ่)": 18, "สูง": 22, "ปานกลาง": 26}[q]
        bitrate = {"สูงมาก (ไฟล์ใหญ่)": "60M", "สูง": "30M", "ปานกลาง": "12M"}[q]

        enc_map = {
            "อัตโนมัติ (CPU)": "x264",
            "NVIDIA GPU (NVENC)": "nvenc",
            "AMD GPU (AMF)": "amf",
            "Intel (QSV)": "qsv",
        }
        encoder = enc_map[self.enc_menu.get()]

        sys_dev = None
        if self.sys_switch.get():
            sys_dev = self.sys_map.get(self.sys_menu.get())
        mic_dev = None
        if self.mic_switch.get():
            mic_dev = self.mic_map.get(self.mic_menu.get())

        return {
            "region": self.region,
            "scale": scale,
            "fps": fps,
            "crf": crf,
            "cq": cq,
            "bitrate": bitrate,
            "encoder": encoder,
            "x264_preset": "medium",
            "sys_dev": sys_dev,
            "mic_dev": mic_dev,
            "out_dir": self.out_dir,
            "sync_ms": int(self._sync_ms),
        }

    def _on_rec_btn(self):
        if self.rec_state == "idle":
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        if not self.ffmpeg:
            return self._warn_no_ffmpeg()

        # อ่านปลายทางจากช่องที่พิมพ์/เลือกไว้ตอนนี้ แล้วใช้ค่านี้ทั้งไฟล์ชั่วคราวและไฟล์จริง
        typed = self.out_entry.get().strip().strip('"')
        if typed:
            self.out_dir = typed
        if not os.path.isdir(self.out_dir):
            try:
                os.makedirs(self.out_dir, exist_ok=True)
            except Exception:
                messagebox.showerror(
                    "ผิดพลาด",
                    f"โฟลเดอร์ปลายทางไม่ถูกต้องหรือสร้างไม่ได้:\n{self.out_dir}")
                return

        settings = self._collect_settings()
        self._save_settings()   # จำค่าไว้ใช้ครั้งต่อไป

        # เตือนถ้าเปิดสวิตช์เสียงไว้แต่ยังเลือกอุปกรณ์ไม่ถูกต้อง
        problems = []
        if self.sys_switch.get() and not settings["sys_dev"]:
            problems.append("เสียงระบบ")
        if self.mic_switch.get() and not settings["mic_dev"]:
            problems.append("ไมโครโฟน")
        if problems:
            extra = ""
            if not HAS_PYAUDIO:
                extra = ("\n\n** ยังไม่ได้ติดตั้งไลบรารีอัดเสียง **\n"
                         "เปิด PowerShell พิมพ์:  pip install pyaudiowpatch\n"
                         "แล้วเปิดโปรแกรมใหม่")
            ok = messagebox.askyesno(
                "อุปกรณ์เสียงยังไม่พร้อม",
                f"ยังเลือกอุปกรณ์สำหรับ: {', '.join(problems)} ไม่ได้\n"
                "ลองกด \"🔄 ตรวจหาอุปกรณ์เสียงใหม่\" ก่อน" + extra +
                "\n\nต้องการบันทึกต่อโดยไม่มีเสียงส่วนนั้นไหม?",
            )
            if not ok:
                return

        try:
            self.recorder.start(settings)
        except Exception as e:
            messagebox.showerror("เริ่มบันทึกไม่สำเร็จ", str(e))
            return

        self.rec_state = "recording"
        self.elapsed = 0.0
        self._seg_start = time.time()
        self.rec_btn.configure(text="■  หยุดบันทึก", fg_color="#444",
                               hover_color="#555")
        self.pause_btn.configure(state="normal")
        self.status_lbl.configure(text="🔴 กำลังบันทึก…", text_color=DANGER)
        self._audio_flags = (bool(settings.get("sys_dev")),
                             bool(settings.get("mic_dev")))
        self._update_border()
        self._show_audio_indicator()

    def _on_pause_btn(self):
        if self.rec_state == "recording":
            self.recorder.pause()
            self.elapsed += time.time() - self._seg_start
            self.rec_state = "paused"
            self.pause_btn.configure(text="▶")
            self.status_lbl.configure(text="⏸ พักชั่วคราว", text_color="#FFB020")
            self._update_border()
            self.audio_ind.hide()
        elif self.rec_state == "paused":
            self.recorder.resume()
            self._seg_start = time.time()
            self.rec_state = "recording"
            self.pause_btn.configure(text="⏸")
            self.status_lbl.configure(text="🔴 กำลังบันทึก…", text_color=DANGER)
            self._update_border()
            self._show_audio_indicator()

    def _stop_recording(self):
        if self.rec_state == "recording":
            self.elapsed += time.time() - self._seg_start
        self.rec_state = "saving"
        self.audio_ind.hide()
        self.rec_btn.configure(state="disabled", text="กำลังบันทึกไฟล์…")
        self.pause_btn.configure(state="disabled")
        self.status_lbl.configure(text="💾 กำลังรวมและบันทึกไฟล์…", text_color=ACCENT)

        out_path = os.path.join(
            self.out_dir, f"บันทึก_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        )
        threading.Thread(target=self._finalize, args=(out_path,), daemon=True).start()

    def _finalize(self, out_path):
        ok = False
        try:
            ok = self.recorder.stop(out_path)
        except Exception:
            ok = False
        self.after(0, lambda: self._after_save(ok, out_path))

    def _after_save(self, ok, out_path):
        self.rec_state = "idle"
        self.elapsed = 0.0
        try:
            self.rec_btn.configure(state="normal", text="●  เริ่มบันทึก",
                                   fg_color=DANGER, hover_color=DANGER_HOVER)
            self.pause_btn.configure(state="disabled", text="⏸")
            self.timer_lbl.configure(text="00:00:00")
            self._update_border()
        except Exception:
            pass
        if ok:
            self.status_lbl.configure(text="✅ บันทึกสำเร็จ", text_color=SUCCESS)
            if messagebox.askyesno(
                    "สำเร็จ",
                    f"บันทึกไฟล์แล้วที่:\n{out_path}\n\nเปิดโฟลเดอร์เลยไหม?"):
                self._open_folder(out_path)
        else:
            self.status_lbl.configure(text="❌ บันทึกไม่สำเร็จ", text_color=DANGER)
            messagebox.showerror(
                "ไม่สำเร็จ",
                "บันทึกไม่สำเร็จ\n"
                "ลองปิดสวิตช์เสียง/ไมค์ แล้วบันทึกใหม่อีกครั้ง\n"
                "(ดูรายละเอียดข้อผิดพลาดได้ในไฟล์ ffmpeg.log ในโฟลเดอร์ปลายทาง)",
            )

    def _open_folder(self, path):
        folder = os.path.dirname(path)
        try:
            if IS_WINDOWS:
                os.startfile(folder)
            elif IS_MAC:
                subprocess.run(["open", folder])
            else:
                subprocess.run(["xdg-open", folder])
        except Exception:
            pass

    # ---------- ลูปอัปเดตเวลา ----------
    def _refresh_status_loop(self):
        if self.rec_state == "recording" and self._seg_start:
            total = self.elapsed + (time.time() - self._seg_start)
        else:
            total = self.elapsed
        if self.rec_state in ("recording", "paused"):
            h = int(total // 3600)
            m = int((total % 3600) // 60)
            s = int(total % 60)
            self.timer_lbl.configure(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.after(250, self._refresh_status_loop)


if __name__ == "__main__":
    app = App()
    app.mainloop()
