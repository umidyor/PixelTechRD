import asyncio
import websockets
import json
import ssl
import base64
from PIL import Image, ImageTk
import io
import tkinter as tk
from tkinter import messagebox
import threading
from pynput import keyboard
import time
from collections import deque
from PIL import Image, ImageTk, ImageFilter, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# SSL (self-signed uchun)
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# Vizual sozlamalar
LETTERBOX_BG   = (16, 16, 16)  # kanvas bilan uyg'un qoramtir fon
UNSHARP_RADIUS = 0.5           # matn va chiziqlarni tiniqlash
UNSHARP_PCT    = 70
UNSHARP_TH     = 2


def resize_to_canvas_hq(pil_img: Image.Image, canvas_w: int, canvas_h: int) -> Image.Image:
    """
    Rasmni kanvasga proportsiyani saqlagan holda sig'diradi (letterbox),
    LANCZOS bilan pasaytiradi va yengil Unsharp qo'llaydi.
    """
    if canvas_w < 2 or canvas_h < 2:
        return pil_img

    # manba RGB bo'lsin (ba'zi WebP/PNG'larda RGBA bo'lishi mumkin)
    if pil_img.mode not in ("RGB", "L"):
        pil_img = pil_img.convert("RGB")

    src_w, src_h = pil_img.width, pil_img.height
    scale = min(canvas_w / src_w, canvas_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))

    # LANCZOS — pastga o'lchashda eng sifatli
    img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    # Matn/chegaralarni tiniqlash uchun yengil unsharp
    img = img.filter(ImageFilter.UnsharpMask(radius=UNSHARP_RADIUS,
                                             percent=UNSHARP_PCT,
                                             threshold=UNSHARP_TH))

    # Letterbox: markazga joylashtiramiz, fon to'ldiramiz
    if new_w != canvas_w or new_h != canvas_h:
        bg = Image.new("RGB", (canvas_w, canvas_h), LETTERBOX_BG)
        off_x = (canvas_w - new_w) // 2
        off_y = (canvas_h - new_h) // 2
        bg.paste(img, (off_x, off_y))
        img = bg

    return img


class OperatorApp:
    def __init__(self):
        self.ws = None
        self.room = None
        self.running = False
        self.loop = None
        self.canvas = None
        self.current_image = None
        self.canvas_image_id = None

        # Client kadr o'lchami (client yuborgan width/height)
        self.client_width = 1
        self.client_height = 1

        # FPS & Performance
        self.frame_count = 0
        self.fps = 0
        self.last_fps_time = time.time()
        self.frame_queue = deque(maxlen=3)  # Buffer 3 frame

        # Keyboard
        self.pressed_keys = set()
        self.keyboard_listener = None
        self.keyboard_enabled = False

        self.client_connected = False

        # Mouse throttle
        self.last_mouse_send = time.time()
        self.mouse_throttle = 0.016  # 60 FPS max

        # Canvas size cache
        self.cached_canvas_width = 0
        self.cached_canvas_height = 0

        # Chizilgan rasmning kanvasdagi aniq joyi (letterbox bilan)
        # (x0, y0, disp_w, disp_h)
        self.last_draw_rect = None

    async def connect(self, room, canvas, status_callback, fps_callback, waiting_callback):
        self.room = room
        self.canvas = canvas
        self.status_callback = status_callback
        self.fps_callback = fps_callback
        self.waiting_callback = waiting_callback

        uri = f"wss://deskweb.duckdns.org/ws/{room}/operator"

        try:
            async with websockets.connect(
                uri,
                ssl=ssl_context,
                ping_interval=20,
                ping_timeout=10,
                max_size=10 * 1024 * 1024
            ) as ws:
                self.ws = ws
                self.running = True
                self.status_callback("waiting_client")
                self.waiting_callback(True)

                self.start_keyboard_listener()
                await self.receive_frames()

        except Exception as e:
            print(f"Connection error: {e}")
            self.running = False
            self.status_callback("error")
        finally:
            self.stop_keyboard_listener()

    def start_keyboard_listener(self):
        self.keyboard_enabled = True

        def on_press(key):
            if not self.keyboard_enabled or not self.running or not self.client_connected:
                return

            try:
                key_name = None
                if hasattr(key, 'char') and key.char:
                    key_name = key.char
                elif hasattr(key, 'name'):
                    key_name = key.name
                else:
                    key_name = str(key).replace('Key.', '')

                if key_name and key_name not in self.pressed_keys:
                    self.pressed_keys.add(key_name)

                    hotkey = self.detect_hotkey()
                    if hotkey:
                        self.send_command({'type': 'hotkey', 'keys': hotkey})
                        return True

                    if len(key_name) == 1:
                        self.send_command({'type': 'type_text', 'text': key_name})
                    else:
                        self.send_command({'type': 'key_down', 'key': key_name})

                    return True

            except Exception as e:
                print(f"Key press error: {e}")

        def on_release(key):
            if not self.keyboard_enabled or not self.running or not self.client_connected:
                return

            try:
                key_name = None
                if hasattr(key, 'char') and key.char:
                    key_name = key.char
                elif hasattr(key, 'name'):
                    key_name = key.name
                else:
                    key_name = str(key).replace('Key.', '')

            #    if key_name:
                if key_name:
                    self.pressed_keys.discard(key_name)
                    if len(key_name) > 1:
                        self.send_command({'type': 'key_up', 'key': key_name})

                return True

            except Exception as e:
                print(f"Key release error: {e}")

        self.keyboard_listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release,
            suppress=True
        )
        self.keyboard_listener.start()

    def stop_keyboard_listener(self):
        self.keyboard_enabled = False
        if self.keyboard_listener:
            self.keyboard_listener.stop()

    def detect_hotkey(self):
        keys = list(self.pressed_keys)

        if 'ctrl_l' in keys or 'ctrl_r' in keys:
            if 'shift' in keys or 'shift_l' in keys or 'shift_r' in keys:
                if 'esc' in keys:
                    return ['ctrl', 'shift', 'esc']

        if 'cmd' in keys or 'cmd_l' in keys or 'cmd_r' in keys:
            for k, v in [('r', 'win+r'), ('e', 'win+e'), ('l', 'win+l'), ('d', 'win+d'), ('x', 'win+x')]:
                if k in keys:
                    return ['win', k]

        if 'ctrl_l' in keys or 'ctrl_r' in keys or 'ctrl' in keys:
            for k in ['c', 'v', 'x', 'a', 'z', 'y', 's', 'f', 'w', 't', 'r', 'n', 'o', 'p', 'h']:
                if k in keys:
                    return ['ctrl', k]

        if 'alt_l' in keys or 'alt_r' in keys or 'alt' in keys:
            if 'tab' in keys:
                return ['alt', 'tab']
            elif 'f4' in keys:
                return ['alt', 'f4']

        return None

    async def receive_frames(self):
        try:
            async for message in self.ws:
                if not self.running:
                    break

                try:
                    data = json.loads(message)

                    if data.get('type') == 'screen':
                        if not self.client_connected:
                            self.client_connected = True
                            self.status_callback("connected")
                            self.waiting_callback(False)

                        # Client kadr o'lchamlarini eslab qolamiz (mapping uchun zarur)
                        self.client_width = int(data.get('width', self.client_width) or self.client_width)
                        self.client_height = int(data.get('height', self.client_height) or self.client_height)

                        # Queue'ga qo'shamiz, keyin eng so'nggi kadrni chizamiz
                        self.frame_queue.append(data)
                        if len(self.frame_queue) > 0:
                            latest_frame = self.frame_queue.pop()
                            self.process_frame(latest_frame)
                            self.frame_queue.clear()

                    elif data.get('type') == 'peer_disconnected':
                        self.client_connected = False
                        self.running = False
                        self.status_callback("client_disconnected")
                        messagebox.showinfo("Disconnected", "Client disconnected.")
                        break

                    elif data.get('type') == 'peer_connected':
                        self.client_connected = True
                        self.status_callback("connected")
                        self.waiting_callback(False)

                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    print(f"Frame error: {e}")

        except websockets.exceptions.ConnectionClosed:
            self.running = False
            self.status_callback("disconnected")
        except Exception as e:
            print(f"Receive error: {e}")
            self.running = False

    def process_frame(self, msg):
        """Canvas'ni FLICKER'siz yangilash (JSON dan kelgan kadrni chizish) va draw_rect saqlash."""
        try:
            # msg — {'type':'screen','data': '...','width':..,'height':..}
            if isinstance(msg, dict):
                img_b64 = msg.get('data')
                src_w = int(msg.get('width', self.client_width) or self.client_width)
                src_h = int(msg.get('height', self.client_height) or self.client_height)
            else:
                img_b64 = msg
                src_w, src_h = self.client_width, self.client_height

            if not img_b64:
                return

            img_bytes = base64.b64decode(img_b64)
            pil_img = Image.open(io.BytesIO(img_bytes))

            # Canvas o‘lchami
            canvas_w = self.canvas.winfo_width()
            canvas_h = self.canvas.winfo_height()
            if canvas_w < 10 or canvas_h < 10:
                return

            # Yuqori sifatli resize + letterbox (ko‘rinish uchun)
            display_img = resize_to_canvas_hq(pil_img, canvas_w, canvas_h)

            # Chizilgan rasmning haqiqiy rect'ini hisoblaymiz (mapping uchun):
            # Bu resize_to_canvas_hq ichidagi hisoblash bilan bir xil.
            scale = min(canvas_w / max(1, src_w), canvas_h / max(1, src_h))
            disp_w = max(1, int(src_w * scale))
            disp_h = max(1, int(src_h * scale))
            x0 = (canvas_w - disp_w) // 2
            y0 = (canvas_h - disp_h) // 2
            self.last_draw_rect = (x0, y0, disp_w, disp_h)

            # Bir martalik image id: flicker bo‘lmasin
            if self.canvas_image_id is None:
                self.current_image = ImageTk.PhotoImage(display_img)
                self.canvas_image_id = self.canvas.create_image(
                    canvas_w // 2, canvas_h // 2,
                    image=self.current_image,
                    anchor='center'
                )
            else:
                self.current_image = ImageTk.PhotoImage(display_img)
                self.canvas.itemconfig(self.canvas_image_id, image=self.current_image)

            # FPS hisoblash
            self.frame_count += 1
            now = time.time()
            if now - self.last_fps_time >= 1.0:
                self.fps = self.frame_count
                self.frame_count = 0
                self.last_fps_time = now
                if hasattr(self, 'fps_callback') and self.fps_callback:
                    self.fps_callback(self.fps)

        except Exception as e:
            print(f"Canvas update error: {e}")

    def send_command(self, cmd):
        if self.ws and self.running and self.client_connected:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.ws.send(json.dumps(cmd)),
                    self.loop
                )
            except Exception as e:
                print(f"Send error: {e}")

    # --- YANGI: canvas -> norm mapping (letterbox bilan aniq) ---
    def _canvas_to_norm(self, mx: int, my: int):
        """
        Canvas koordinatasini (mx,my) chizilgan rasmning ichidagi 0..1 oralig‘iga o'tkazadi.
        Agar bosish letterbox hududida bo'lsa -> (None, None) qaytaradi.
        """
        if not self.last_draw_rect:
            return None, None
        x0, y0, disp_w, disp_h = self.last_draw_rect
        if mx < x0 or my < y0 or mx > x0 + disp_w or my > y0 + disp_h:
            return None, None
        xn = (mx - x0) / max(1, disp_w)
        yn = (my - y0) / max(1, disp_h)
        # xavfsizlik
        xn = max(0.0, min(1.0, xn))
        yn = max(0.0, min(1.0, yn))
        return xn, yn

    def send_mouse(self, x, y, cmd_type='mouse_move'):
        """Mouse yuborish: throttling + letterbox-aware norm mapping."""
        current_time = time.time()

        if cmd_type == 'mouse_move':
            if current_time - self.last_mouse_send < self.mouse_throttle:
                return
            self.last_mouse_send = current_time

        if not (self.ws and self.running and self.client_connected):
            return

        try:
            xn, yn = self._canvas_to_norm(x, y)
            if xn is None:
                # Letterboxga bosilgan — e'tiborsiz qoldiramiz.
                return

            if cmd_type == 'mouse_move':
                self.send_command({'type': 'mouse_move', 'x': xn, 'y': yn})
            elif cmd_type == 'mouse_click':
                self.send_command({'type': 'mouse_click', 'x': xn, 'y': yn, 'button': 'left'})
            elif cmd_type == 'mouse_right':
                self.send_command({'type': 'mouse_click', 'x': xn, 'y': yn, 'button': 'right'})

        except Exception as e:
            print(f"Mouse error: {e}")

    def disconnect(self):
        self.running = False
        self.client_connected = False
        self.stop_keyboard_listener()
        if self.ws and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)
            except:
                pass


class OperatorGUI:
    def __init__(self):
        self.app = OperatorApp()
        self.root = tk.Tk()
        self.root.title("DeskWeb Operator")
        self.root.geometry("1280x800")
        self.root.minsize(800, 600)

        self.connected = False
        self.setup_ui()

    def setup_ui(self):
        # Login
        self.login_frame = tk.Frame(self.root, bg="#1a1a2e")
        self.login_frame.pack(fill='both', expand=True)

        tk.Label(self.login_frame, text="🖥️ DeskWeb Operator",
                 font=("Arial", 28, "bold"), fg="white", bg="#1a1a2e").pack(pady=(150, 20))

        tk.Label(self.login_frame, text="Enter Room ID",
                 font=("Arial", 12), fg="#8b949e", bg="#1a1a2e").pack(pady=10)

        self.room_var = tk.StringVar()
        room_entry = tk.Entry(self.login_frame, textvariable=self.room_var,
                              font=("Arial", 16), width=30, justify='center')
        room_entry.pack(pady=20, ipady=10)
        room_entry.focus_set()

        tk.Button(self.login_frame, text="🔗 Connect",
                  command=self.on_connect,
                  font=("Arial", 14, "bold"),
                  bg="#28a745", fg="white",
                  padx=40, pady=12, cursor="hand2").pack(pady=20)

        room_entry.bind('<Return>', lambda e: self.on_connect())

        # Control
        self.control_frame = tk.Frame(self.root, bg="#0d1117")

        # Toolbar
        toolbar = tk.Frame(self.control_frame, bg="#161b22", height=55)
        toolbar.pack(fill='x')
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text="Room:", font=("Arial", 10),
                 fg="#8b949e", bg="#161b22").pack(side='left', padx=(15, 5), pady=15)

        self.room_label = tk.Label(toolbar, text="", font=("Consolas", 11, "bold"),
                                   fg="#58a6ff", bg="#161b22")
        self.room_label.pack(side='left', pady=15)

        self.status_label = tk.Label(toolbar, text="⏳ Connecting...",
                                     font=("Arial", 10), fg="#ffc107", bg="#161b22")
        self.status_label.pack(side='left', padx=20, pady=15)

        tk.Label(toolbar, text="FPS:", font=("Arial", 10),
                 fg="#8b949e", bg="#161b22").pack(side='left', padx=(20, 5), pady=15)

        self.fps_label = tk.Label(toolbar, text="0", font=("Consolas", 11, "bold"),
                                  fg="#58a6ff", bg="#161b22")
        self.fps_label.pack(side='left', pady=15)

        tk.Button(toolbar, text="❌ Disconnect",
                  command=self.on_disconnect,
                  font=("Arial", 10, "bold"),
                  bg="#dc3545", fg="white",
                  padx=20, pady=8, cursor="hand2").pack(side='right', padx=15, pady=10)

        # Canvas
        canvas_frame = tk.Frame(self.control_frame, bg="#000000")
        canvas_frame.pack(fill='both', expand=True, padx=10, pady=10)

        self.canvas = tk.Canvas(canvas_frame, bg="#000000", highlightthickness=0,
                                bd=0, relief='flat')
        self.canvas.pack(fill='both', expand=True)

        self.waiting_label = tk.Label(self.canvas,
                                      text="⏳ Waiting for client...\n\nShare Room ID",
                                      font=("Arial", 14), fg="#ffc107", bg="#000000",
                                      justify='center')

        # Mouse events
        self.canvas.bind('<Motion>', self.on_mouse_move)
        self.canvas.bind('<Button-1>', self.on_mouse_click)
        self.canvas.bind('<Button-3>', self.on_right_click)
        self.canvas.bind('<MouseWheel>', self.on_scroll)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_connect(self):
        room = self.room_var.get().strip()
        if not room:
            messagebox.showwarning("Warning", "Enter Room ID")
            return

        self.room_label.config(text=room)
        self.login_frame.pack_forget()
        self.control_frame.pack(fill='both', expand=True)
        self.connected = True

        def run():
            self.app.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.app.loop)
            try:
                self.app.loop.run_until_complete(
                    self.app.connect(room, self.canvas, self.update_status,
                                     self.update_fps, self.update_waiting)
                )
            except Exception as e:
                print(f"Error: {e}")
            finally:
                self.root.after(0, self.on_disconnect)

        threading.Thread(target=run, daemon=True).start()

    def on_disconnect(self):
        self.app.disconnect()
        self.connected = False
        self.control_frame.pack_forget()
        self.login_frame.pack(fill='both', expand=True)
        self.room_var.set("")

    def update_status(self, status):
        status_map = {
            "waiting_client": ("⏳ Waiting", "#ffc107"),
            "connected": ("🟢 Connected", "#28a745"),
            "client_disconnected": ("⚠️ Client Left", "#dc3545"),
            "disconnected": ("🔴 Disconnected", "#dc3545"),
            "error": ("⚠️ Error", "#ffc107")
        }
        text, color = status_map.get(status, ("Unknown", "#8b949e"))
        self.status_label.config(text=text, fg=color)

    def update_waiting(self, show):
        if show:
            self.waiting_label.place(relx=0.5, rely=0.5, anchor='center')
        else:
            self.waiting_label.place_forget()

    def update_fps(self, fps):
        self.fps_label.config(text=str(fps))

    def on_mouse_move(self, event):
        if self.connected and self.app.client_connected:
            self.app.send_mouse(event.x, event.y, 'mouse_move')

    def on_mouse_click(self, event):
        if self.connected and self.app.client_connected:
            self.app.send_mouse(event.x, event.y, 'mouse_click')

    def on_right_click(self, event):
        if self.connected and self.app.client_connected:
            self.app.send_mouse(event.x, event.y, 'mouse_right')

    def on_scroll(self, event):
        if self.connected and self.app.client_connected:
            # Tkinterda event.delta odatda 120/-120 bo'ladi (Windows)
            # Clientga mantiqiy kichik step yuboramiz:
            delta = 3 if event.delta > 0 else -3
            self.app.send_command({'type': 'scroll', 'delta': delta})

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Exit?"):
            self.app.disconnect()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    gui = OperatorGUI()
    gui.run()
