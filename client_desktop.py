import asyncio
import websockets
import json
import pyautogui
import mss
import io
import base64
import ssl
import time
import ctypes
import tkinter as tk
from tkinter import messagebox
import threading
import os
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageFilter

# --- Windows DPI Awareness (1:1 koordinata aniqligi uchun) ---
try:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # Per-monitor v2
    except Exception:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)          # System DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ------------------ Umumiy sozlamalar ------------------

# SSL tekshiruvini o'chirish (self-signed sertifikatlar uchun)
ssl_context_unverified = ssl.create_default_context()
ssl_context_unverified.check_hostname = False
ssl_context_unverified.verify_mode = ssl.CERT_NONE

# Ekran o'lchami va pyautogui sozlamalari
screen_width, screen_height = pyautogui.size()
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

# FPS va rasm sifat parametrlari
FPS_TARGET      = 30
FRAME_DELAY     = 1.0 / FPS_TARGET
TARGET_WIDTH    = 1024    # 1152 ham mumkin; 1024 bilan FPS barqarorroq
WEBP_QUALITY    = 80
JPEG_QUALITY    = 80
UNSHARP_ENABLED = False    # True qilsangiz matn biroz tiniqroq, lekin FPS pasayadi
UNSHARP_RADIUS  = 0.6
UNSHARP_PCT     = 90
UNSHARP_TH      = 3

# Kodlashni fon oqimida bajarish uchun pool
executor = ThreadPoolExecutor(max_workers=2)

# OpenCV (tez encoder) ixtiyoriy
import numpy as np
try:
    import cv2
    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False


# ------------------ Yordamchi funksiyalar ------------------

def downscale_hq(img: Image.Image, target_width: int) -> Image.Image:
    """
    Pasaytirishni yuqori sifat bilan bajaradi; xohlasa Unsharp qo'yadi.
    """
    if img.width > target_width:
        ratio = target_width / img.width
        new_size = (target_width, max(1, int(img.height * ratio)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        if UNSHARP_ENABLED:
            img = img.filter(ImageFilter.UnsharpMask(radius=UNSHARP_RADIUS,
                                                     percent=UNSHARP_PCT,
                                                     threshold=UNSHARP_TH))
    return img


def _encode_image_sync_pil(pil_img: Image.Image, prefer_webp: bool = True):
    """
    Tez va barqaror PIL encoder (fallback).
    Qaytaradi: (base64_str, "webp"|"jpeg")
    """
    buf = io.BytesIO()
    if prefer_webp:
        try:
            pil_img.save(buf, format="WEBP", quality=WEBP_QUALITY, method=6)
            return base64.b64encode(buf.getvalue()).decode("ascii"), "webp"
        except Exception:
            buf = io.BytesIO()
    # JPEG fallback (subsampling=2 -> 4:2:0 — tez va kichik)
    pil_img.save(buf, format="JPEG", quality=JPEG_QUALITY,
                 optimize=True, subsampling=2, progressive=False)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "jpeg"


def _encode_image_sync_cv2(pil_img: Image.Image, prefer_webp: bool = True):
    """
    OpenCV encoder — odatda PIL’dan ancha tez.
    Qaytaradi: (base64_str, "webp"|"jpeg")
    """
    arr = np.array(pil_img)[:, :, ::-1]  # RGB -> BGR
    if prefer_webp:
        ok, buf = cv2.imencode('.webp', arr, [int(cv2.IMWRITE_WEBP_QUALITY), WEBP_QUALITY])
        if ok:
            return base64.b64encode(buf.tobytes()).decode('ascii'), 'webp'
    # JPEG fallback
    ok, buf = cv2.imencode('.jpg', arr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY,
                                         int(cv2.IMWRITE_JPEG_OPTIMIZE), 1])
    if ok:
        return base64.b64encode(buf.tobytes()).decode('ascii'), 'jpeg'
    # Favqulodda fallback: PIL
    return _encode_image_sync_pil(pil_img, prefer_webp=False)


async def encode_image_async(pil_img: Image.Image, prefer_webp: bool = True):
    """
    Async-API: kodlashni thread poolga chiqaradi. OpenCV bo'lsa — undan foydalanadi.
    """
    loop = asyncio.get_running_loop()
    if CV2_AVAILABLE:
        return await loop.run_in_executor(executor, _encode_image_sync_cv2, pil_img, prefer_webp)
    return await loop.run_in_executor(executor, _encode_image_sync_pil, pil_img, prefer_webp)


# ------------------ Asosiy Client logikasi ------------------

class ClientApp:
    def __init__(self):
        self.ws = None
        self.room = None
        self.running = False
        self.loop = None
        self.send_task = None
        self.recv_task = None
        self.SERVER_URL_BASE = "wss://deskweb.duckdns.org"

    async def connect(self, room: str):
        self.room = room
        uri = f"{self.SERVER_URL_BASE}/ws/{room}/client"
        self.running = True

        try:
            self.ws = await websockets.connect(
                uri,
                ssl=ssl_context_unverified,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=3,
                max_size=10 * 1024 * 1024,
                compression=None   # rasmlar allaqachon siqilgan; CPU ni tejaymiz
            )

            print(f"[Client] Connected to room: {room}")

            # MUHIM: serverga JOIN yuboramiz
            await self.ws.send(json.dumps({
                "type": "join",
                "role": "client",
                "room": self.room
            }))

            # (ixtiyoriy) serverdan tasdiq kutish
            try:
                ack_raw = await asyncio.wait_for(self.ws.recv(), timeout=5)
                try:
                    ack = json.loads(ack_raw)
                    print(f"[Client] Server says: {ack}")
                except json.JSONDecodeError:
                    print("[Client] Non-JSON ACK:", ack_raw)
            except asyncio.TimeoutError:
                pass  # server ack yubormasa ham davom etamiz

            # Parallel ravishda ekran yuborish va buyruqlarni olish
            self.send_task = asyncio.create_task(self.send_screen())
            self.recv_task = asyncio.create_task(self.receive_commands())
            await asyncio.gather(self.send_task, self.recv_task)

        except Exception as e:
            print(f"[Client] Connection error: {e}")
        finally:
            try:
                if self.ws and not self.ws.closed:
                    await self.ws.close()
            except Exception:
                pass

            self.ws = None
            self.running = False
            print("[Client] Connection closed.")

    async def _close_ws(self):
        try:
            if self.ws and not self.ws.closed:
                await self.ws.close()
        except Exception:
            pass
        self.ws = None

    async def send_screen(self):
        """
        Ekran tasvirini WebSocket orqali uzatish (OpenCV/PIL encoder + threadpool + FPS control).
        """
        sct = mss.mss()
        last_fps_time = time.time()
        frame_count = 0

        # Monitor tanlash (1-chi monitor odatda butun ish stoli)
        try:
            monitor = sct.monitors[1]
        except Exception:
            monitor = sct.monitors[0]

        try:
            while self.running and self.ws:
                # Ekranni olish
                screenshot = sct.grab(monitor)
                img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)

                # Kichraytirish (+ xohlasa unsharp)
                img = downscale_hq(img, TARGET_WIDTH)

                # Kodlash (OpenCV bo'lsa undan foydalanadi; bo'lmasa PIL)
                img_b64, img_fmt = await encode_image_async(img, prefer_webp=True)

                # Paket
                payload = {
                    "type": "screen",
                    "format": img_fmt,
                    "data": img_b64,
                    "width": img.width,
                    "height": img.height,
                    "timestamp": time.time(),
                }

                # Yuborish
                try:
                    await self.ws.send(json.dumps(payload))
                except websockets.exceptions.ConnectionClosed:
                    print("[Client] WS closed while sending frame.")
                    break

                # FPS hisoblash
                frame_count += 1
                now = time.time()
                if now - last_fps_time >= 1.0:
                    print(f"[Client] FPS: {frame_count}")
                    frame_count = 0
                    last_fps_time = now

                # FPS nazorati
                await asyncio.sleep(FRAME_DELAY)

        except asyncio.CancelledError:
            print("[Client] send_screen cancelled.")
        except Exception as e:
            print(f"[Client] Screen send error: {e}")
        finally:
            try:
                sct.close()
            except Exception:
                pass
            self.running = False
            print("[Client] Screen sender stopped.")

    async def receive_commands(self):
        """
        Operator komandalarini qabul qilish va bajarish.
        """
        try:
            async for message in self.ws:
                if not self.running:
                    break

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    print("[Client] Invalid JSON received.")
                    continue

                msg_type = data.get("type")

                # Server holat xabarlari
                if msg_type in ("peer_disconnected", "disconnect", "bye"):
                    print(f"[Client] Server says: {msg_type}")
                    break

                if msg_type in ("connected", "ready", "joined", "ok"):
                    print(f"[Client] Server confirmed: {data}")
                    continue

                # Boshqa komandalar -> alohida thread'da (loopni bloklamaslik uchun)
                if self.running:
                    threading.Thread(target=self.execute_command, args=(data,), daemon=True).start()

        except websockets.exceptions.ConnectionClosed:
            print("[Client] Connection closed by server.")
        except Exception as e:
            print(f"[Client] Receive error: {e}")
        finally:
            self.running = False

    def execute_command(self, data: dict):
        """
        PyAutoGUI orqali komandalarni bajarish.
        """
        if not self.running:
            return

        try:
            cmd_type = data.get('type')

            if cmd_type == 'mouse_move':
                x = int(data['x'] * screen_width)
                y = int(data['y'] * screen_height)
                pyautogui.moveTo(x, y, duration=0, _pause=False)

            elif cmd_type == 'mouse_click':
                x = int(data['x'] * screen_width)
                y = int(data['y'] * screen_height)
                button = data.get('button', 'left')
                pyautogui.click(x, y, button=button, _pause=False)

            elif cmd_type == 'mouse_down':
                x = int(data['x'] * screen_width)
                y = int(data['y'] * screen_height)
                pyautogui.mouseDown(x, y, _pause=False)

            elif cmd_type == 'mouse_up':
                x = int(data['x'] * screen_width)
                y = int(data['y'] * screen_height)
                pyautogui.mouseUp(x, y, _pause=False)

            elif cmd_type == 'scroll':
                pyautogui.scroll(int(data.get('delta', 0)), _pause=False)

            elif cmd_type == 'key_press':
                pyautogui.press(data['key'], _pause=False)

            elif cmd_type == 'key_down':
                pyautogui.keyDown(data['key'], _pause=False)

            elif cmd_type == 'key_up':
                pyautogui.keyUp(data['key'], _pause=False)

            elif cmd_type == 'type_text':
                pyautogui.write(data.get('text', ''), interval=0, _pause=False)

            elif cmd_type == 'hotkey':
                keys = data.get('keys', [])
                keys_str = '+'.join(keys)

                # Windows tugmasi nomini moslashtiramiz
                if 'win' in keys:
                    keys = [k if k != 'win' else 'winleft' for k in keys]

                special = {
                    'ctrl+shift+esc': lambda: (pyautogui.keyDown('ctrl'), pyautogui.keyDown('shift'),
                                               pyautogui.press('esc'), pyautogui.keyUp('shift'), pyautogui.keyUp('ctrl')),
                    'win+r': lambda: (pyautogui.keyDown('winleft'), pyautogui.press('r'), pyautogui.keyUp('winleft')),
                    'win+e': lambda: (pyautogui.keyDown('winleft'), pyautogui.press('e'), pyautogui.keyUp('winleft')),
                    'win+l': lambda: (pyautogui.keyDown('winleft'), pyautogui.press('l'), pyautogui.keyUp('winleft')),
                    'win+d': lambda: (pyautogui.keyDown('winleft'), pyautogui.press('d'), pyautogui.keyUp('winleft')),
                    'win+x': lambda: (pyautogui.keyDown('winleft'), pyautogui.press('x'), pyautogui.keyUp('winleft')),
                    'win+i': lambda: (pyautogui.keyDown('winleft'), pyautogui.press('i'), pyautogui.keyUp('winleft')),
                    'win+tab': lambda: (pyautogui.keyDown('winleft'), pyautogui.press('tab'), pyautogui.keyUp('winleft')),
                    'alt+tab': lambda: (pyautogui.keyDown('alt'), pyautogui.press('tab'), pyautogui.keyUp('alt')),
                    'alt+f4': lambda: (pyautogui.keyDown('alt'), pyautogui.press('f4'), pyautogui.keyUp('alt')),
                }

                if keys_str in special:
                    special[keys_str]()
                elif 'ctrl' in keys and 'shift' in keys:
                    other = [k for k in keys if k not in ['ctrl', 'shift']]
                    pyautogui.keyDown('ctrl'); pyautogui.keyDown('shift')
                    for k in other: pyautogui.press(k)
                    pyautogui.keyUp('shift'); pyautogui.keyUp('ctrl')
                elif 'ctrl' in keys:
                    other = [k for k in keys if k != 'ctrl']
                    pyautogui.keyDown('ctrl')
                    for k in other: pyautogui.press(k)
                    pyautogui.keyUp('ctrl')
                elif 'alt' in keys:
                    other = [k for k in keys if k != 'alt']
                    pyautogui.keyDown('alt')
                    for k in other: pyautogui.press(k)
                    pyautogui.keyUp('alt')
                else:
                    try:
                        pyautogui.hotkey(*keys, _pause=False)
                    except Exception:
                        # Fallback ketma-ket bosish
                        for k in keys[:-1]:
                            pyautogui.keyDown(k)
                        if keys:
                            pyautogui.press(keys[-1])
                        for k in reversed(keys[:-1]):
                            pyautogui.keyUp(k)

        except Exception as e:
            print(f"[Client] Execute error: {e}")

    def _release_modifiers(self):
        try:
            for k in ('ctrl', 'alt', 'shift', 'winleft'):
                try:
                    pyautogui.keyUp(k)
                except Exception:
                    pass
        except Exception:
            pass

    def cleanup(self):
        """
        GUI thread’dan chaqiriladi. Event loop ichida WS yopish uchun threadsafe chaqiramiz.
        """
        print("[Client] Cleaning up...")
        self.running = False
        self._release_modifiers()

        # WebSocketni yopish
        if self.ws and self.loop and self.loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._close_ws(), self.loop)
                try:
                    fut.result(timeout=2)
                except Exception:
                    pass
            except Exception:
                pass

        self.ws = None
        print("[Client] Cleanup complete.")

    def disconnect(self):
        self.cleanup()


# ------------------ GUI ------------------

class ClientGUI:
    def __init__(self):
        self.app = ClientApp()
        self.root = tk.Tk()
        self.root.title("DeskWeb Client - Remote Support")
        self.root.geometry("500x320")
        self.root.resizable(False, False)
        self.setup_ui()

    def setup_ui(self):
        header_frame = tk.Frame(self.root, bg="#007bff", height=80)
        header_frame.pack(fill='x')
        header_frame.pack_propagate(False)
        tk.Label(header_frame, text="🖥️ DeskWeb Client",
                 font=("Arial", 20, "bold"), fg="white", bg="#007bff").pack(pady=25)

        content_frame = tk.Frame(self.root, bg="white")
        content_frame.pack(fill='both', expand=True, padx=30, pady=20)

        tk.Label(content_frame, text="Room ID:",
                 font=("Arial", 11), bg="white", fg="#333").pack(anchor='w')

        self.room_var = tk.StringVar()
        self.entry = tk.Entry(content_frame, textvariable=self.room_var,
                              font=("Arial", 14), width=30, relief='solid', bd=1)
        self.entry.pack(pady=(5, 15), ipady=8)
        self.entry.focus_set()

        btn_frame = tk.Frame(content_frame, bg="white")
        btn_frame.pack(pady=10)

        self.connect_btn = tk.Button(btn_frame, text="Connect",
                                     command=self.on_connect,
                                     font=("Arial", 12, "bold"),
                                     bg="#28a745", fg="white",
                                     padx=30, pady=8, relief='flat',
                                     cursor="hand2")
        self.connect_btn.pack(side='left', padx=5)

        self.disconnect_btn = tk.Button(btn_frame, text="Disconnect",
                                        command=self.on_disconnect,
                                        font=("Arial", 12, "bold"),
                                        bg="#dc3545", fg="white",
                                        padx=30, pady=8, relief='flat',
                                        cursor="hand2", state='disabled')
        self.disconnect_btn.pack(side='left', padx=5)

        self.status_label = tk.Label(content_frame, text="● Not connected",
                                     font=("Arial", 10), fg="#6c757d", bg="white")
        self.status_label.pack(pady=(15, 5))

        warning_frame = tk.Frame(self.root, bg="#fff3cd", relief='solid', bd=1)
        warning_frame.pack(fill='x', padx=20, pady=(0, 20))
        tk.Label(warning_frame, text="⚠️ Your screen will be shared and controlled",
                 font=("Arial", 9), fg="#856404", bg="#fff3cd").pack(pady=8)

        self.entry.bind('<Return>', lambda e: self.on_connect())
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_connect(self):
        room = self.room_var.get().strip()
        if not room:
            messagebox.showwarning("Warning", "Please enter Room ID")
            return

        self.connect_btn.config(state='disabled')
        self.disconnect_btn.config(state='normal')
        self.entry.config(state='disabled')
        self.status_label.config(text="● Connecting...", fg="#ffc107")
        self.root.update()

        def run_connection():
            self.app.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.app.loop)
            try:
                self.app.loop.run_until_complete(self.app.connect(room))
            except Exception as e:
                print(f"[Client] Connection error (loop): {e}")
            finally:
                # Ulanish tugasa UI ni disconnect holatiga qaytaramiz
                self.root.after(0, self.on_disconnect)

        threading.Thread(target=run_connection, daemon=True).start()

    def on_disconnect(self):
        self.app.disconnect()
        self.connect_btn.config(state='normal')
        self.disconnect_btn.config(state='disabled')
        self.entry.config(state='normal')
        self.status_label.config(text="● Disconnected", fg="#dc3545")

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Are you sure you want to exit?"):
            self.app.disconnect()
            self.root.destroy()
            os._exit(0)

    def run(self):
        self.root.mainloop()


# ------------------ Main ------------------

if __name__ == "__main__":
    try:
        gui = ClientGUI()
        gui.run()
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        os._exit(0)
