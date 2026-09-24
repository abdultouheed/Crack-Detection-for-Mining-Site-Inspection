import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

import cv2
import numpy as np
from PIL import Image, ImageTk
from ultralytics import YOLO

BG = "#12161C"          # app background
SURFACE = "#1A2029"     # sidebar / viewer
SURFACE_2 = "#232B38"   # tiles, secondary buttons
BORDER = "#2E3847"
TEXT = "#E8ECF1"
MUTED = "#8B96A5"
ACCENT = "#F5A623"      # hi-vis amber
ACCENT_HOVER = "#FFB94D"
ACCENT_TEXT = "#1A1204"
OK = "#34D399"
DANGER = "#F87171"
INFO = "#60A5FA"
DISABLED_BG = "#1D242E"
DISABLED_FG = "#566070"

UI = "Helvetica"  # replaced with a better system font at startup
SCALE = 1.0       # set from the screen DPI at startup


def px(n):
    """Scale a pixel size for high-DPI screens."""
    return int(n * SCALE)

BG_FILE = "mining_bg.png"   # your background image (next to this script)
WINDOW_DIM = 0.60           # 0 = original image, 1 = solid dark; window backdrop
VIEWER_DIM = 0.45           # same, for the inspection view backdrop

STATUS_STYLES = {
    "idle": (SURFACE_2, MUTED),
    "busy": (INFO, "#0B1220"),
    "crack": (DANGER, "#2A0A0A"),
    "clear": (OK, "#04241A"),
}


def rounded_points(x1, y1, x2, y2, r):
    """Polygon points for a rounded rectangle (use with smooth=True)."""
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def load_background():
    """Find mining_bg.png next to the script or in the working folder."""
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.getcwd()):
        path = os.path.join(folder, BG_FILE)
        if os.path.exists(path):
            try:
                return Image.open(path).convert("RGB")
            except Exception as exc:
                print(f"Could not open {path}: {exc}")
                return None
    print(f"Note: '{BG_FILE}' not found - using a plain background.")
    return None


def cover_dim(image, size, dim):
    """Scale image to fill `size` (cropping overflow), then darken it."""
    w, h = size
    scale = max(w / image.width, h / image.height)
    resized = image.resize((max(int(image.width * scale), 1),
                            max(int(image.height * scale), 1)), Image.Resampling.LANCZOS)
    left, top = (resized.width - w) // 2, (resized.height - h) // 2
    cropped = resized.crop((left, top, left + w, top + h))
    return Image.blend(cropped, Image.new("RGB", (w, h), BG), dim)


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, *, width=100, height=44, radius=10,
                 bg=SURFACE_2, fg=TEXT, hover=BORDER, parent_bg=SURFACE):
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.command, self.radius = text, command, radius
        self.colors = (bg, fg, hover)
        self.enabled = True
        self.hovering = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_hover(self, value):
        self.hovering = value
        self._draw()

    def _on_release(self, event):
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if self.enabled and inside:
            self.command()

    def set_text(self, text):
        self.text = text
        self._draw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        bg, fg, hover = self.colors
        if not self.enabled:
            fill, text_color = DISABLED_BG, DISABLED_FG
        else:
            fill, text_color = (hover if self.hovering else bg), fg
        self.create_polygon(rounded_points(1, 1, w - 1, h - 1, self.radius),
                            smooth=True, fill=fill, outline=fill)
        self.create_text(w / 2, h / 2, text=self.text, fill=text_color,
                         font=(UI, 11, "bold"))


class Slider(tk.Canvas):
    """Flat slider with an amber fill and round handle."""

    def __init__(self, parent, from_, to, value, step, command, parent_bg=SURFACE):
        super().__init__(parent, height=30, bg=parent_bg, highlightthickness=0,
                         cursor="hand2")
        self.from_, self.to, self.value, self.step, self.command = from_, to, value, step, command
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_mouse)
        self.bind("<B1-Motion>", self._on_mouse)

    def _draw(self):
        self.delete("all")
        w, pad, y = self.winfo_width(), 10, 15
        x = pad + (self.value - self.from_) / (self.to - self.from_) * (w - 2 * pad)
        self.create_line(pad, y, w - pad, y, width=4, fill=BORDER, capstyle="round")
        self.create_line(pad, y, x, y, width=4, fill=ACCENT, capstyle="round")
        self.create_oval(x - 8, y - 8, x + 8, y + 8, fill=TEXT, outline=ACCENT, width=2)

    def _on_mouse(self, event):
        w, pad = self.winfo_width(), 10
        frac = min(max((event.x - pad) / max(w - 2 * pad, 1), 0), 1)
        raw = self.from_ + frac * (self.to - self.from_)
        self.value = round(round(raw / self.step) * self.step, 2)
        self._draw()
        self.command(self.value)


class StatTile(tk.Frame):
    def __init__(self, parent, caption):
        super().__init__(parent, bg=SURFACE_2, padx=12, pady=10,
                         highlightthickness=1, highlightbackground=BORDER)
        self.value = tk.Label(self, text="–", bg=SURFACE_2, fg=TEXT, font=(UI, 20, "bold"))
        self.value.pack(anchor="w")
        tk.Label(self, text=caption, bg=SURFACE_2, fg=MUTED, font=(UI, 9)).pack(anchor="w")

    def set(self, text, color=TEXT):
        self.value.config(text=text, fg=color)


class CrackDetectionApp:
    def __init__(self, root):
        self.root = root
        self.model, self.model_error = None, None
        self.conf = 0.25
        self.msgs = queue.Queue()
        self.run_id = 0
        self.stop_event = threading.Event()
        self.running = False
        self.current_image = None
        self.photo = None
        self.bg_image = load_background()
        self.bg_photo = None
        self.viewer_bg_photo = None
        self.viewer_bg_size = None
        self.resize_job = None
        self.play_event = threading.Event()   # set = playing, cleared = paused
        self.play_event.set()
        self.is_video = False
        self.video_path = None
        self.paused = False
        self.finished = False
        self.source_text = "No source loaded"

        self._load_model()
        self._build_ui()
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # model ---------------------------------------------------------------
    def _load_model(self):
        try:
            self.model = YOLO("best.pt")
        except Exception as exc:
            self.model_error = str(exc)

    # layout --------------------------------------------------------------
    def _build_ui(self):
        root = self.root
        root.configure(bg=BG)

        # full-window background image (created first so it sits behind everything)
        self.bg_label = tk.Label(root, bg=BG)
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        root.bind("<Configure>", self._on_window_resize)
        root.after(100, self._update_window_bg)

        sidebar = tk.Frame(root, bg=SURFACE, width=px(350), highlightthickness=1,
                           highlightbackground=BORDER)
        sidebar.pack(side="left", fill="y", padx=(20, 0), pady=20)
        sidebar.pack_propagate(False)

        side = tk.Frame(sidebar, bg=SURFACE)
        side.pack(fill="both", expand=True, padx=24, pady=24)
        # brand
        brand = tk.Frame(side, bg=SURFACE)
        brand.pack(fill="x")
        icon = tk.Canvas(brand, width=40, height=40, bg=SURFACE, highlightthickness=0)
        icon.create_polygon(rounded_points(1, 1, 39, 39, 10), smooth=True, fill=ACCENT, outline=ACCENT)
        icon.create_line(21, 8, 15, 18, 24, 22, 17, 32, width=3, fill=ACCENT_TEXT,
                         joinstyle="round", capstyle="round")
        icon.pack(side="left")
        names = tk.Frame(brand, bg=SURFACE)
        names.pack(side="left", padx=12)
        tk.Label(names, text="Crack Detection", bg=SURFACE, fg=TEXT,
                 font=(UI, 16, "bold")).pack(anchor="w")
        tk.Label(names, text="Structural inspection", bg=SURFACE, fg=MUTED,
                 font=(UI, 10)).pack(anchor="w")

        # source
        self._section(side, "Source")
        self.btn_image = RoundedButton(side, "Open image", self.select_image, bg=ACCENT,
                                       fg=ACCENT_TEXT, hover=ACCENT_HOVER)
        self.btn_image.pack(fill="x")
        self.btn_video = RoundedButton(side, "Open video", self.select_video)
        self.btn_video.pack(fill="x", pady=(10, 0))
        self.btn_play = RoundedButton(side, "Pause video", self.toggle_playback)
        self.btn_play.pack(fill="x", pady=(10, 0))
        self.btn_play.set_enabled(False)

        # metrics
        self._section(side, "Live metrics")
        grid = tk.Frame(side, bg=SURFACE)
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1, uniform="tiles")
        self.tile_cracks = StatTile(grid, "Cracks found")
        self.tile_conf = StatTile(grid, "Confidence")
        self.tile_frames = StatTile(grid, "Frames")
        self.tile_ms = StatTile(grid, "Inference")
        self.tile_cracks.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=(0, 10))
        self.tile_conf.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=(0, 10))
        self.tile_frames.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.tile_ms.grid(row=1, column=1, sticky="nsew", padx=(5, 0))

        # main area: header card, inspection view, footer card
        header = tk.Frame(root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        header.pack(side="top", fill="x", padx=20, pady=(20, 0))
        titles = tk.Frame(header, bg=SURFACE)
        titles.pack(side="left", padx=22, pady=14)
        tk.Label(titles, text="Inspection view", bg=SURFACE, fg=TEXT,
                 font=(UI, 16, "bold")).pack(anchor="w")
        self.source_label = tk.Label(titles, text="No source loaded", bg=SURFACE, fg=MUTED,
                                     font=(UI, 10))
        self.source_label.pack(anchor="w")
        self.pill = tk.Label(header, text="", font=(UI, 11, "bold"), padx=16, pady=7)
        self.pill.pack(side="right", padx=22)
        self._set_status("Waiting for input", "idle")

        footer = tk.Frame(root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        footer.pack(side="bottom", fill="x", padx=20, pady=(0, 20))
        tk.Label(footer, text="Pause a video to save the exact frame you want.", bg=SURFACE,
                 fg=MUTED, font=(UI, 10)).pack(side="left", padx=22, pady=14)
        self.btn_save = RoundedButton(footer, "Save annotated frame", self.save_frame,
                                      width=px(210), height=px(40), parent_bg=SURFACE)
        self.btn_save.pack(side="right", padx=22, pady=10)
        self.btn_save.set_enabled(False)

        self.viewer = tk.Canvas(root, bg=SURFACE, highlightthickness=1,
                                highlightbackground=BORDER)
        self.viewer.pack(fill="both", expand=True, padx=20, pady=16)
        self.viewer.bind("<Configure>", lambda e: self._render())

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=SURFACE, fg=MUTED, font=(UI, 10, "bold")
                 ).pack(anchor="w", pady=(28, 10))

    def _set_status(self, text, kind):
        bg, fg = STATUS_STYLES[kind]
        self.pill.config(text=f"●  {text}", bg=bg, fg=fg)

    def _on_conf(self, value):
        self.conf = value
        self.conf_label.config(text=f"{int(round(value * 100))}%")

    # viewer --------------------------------------------------------------
    def _on_window_resize(self, event):
        if event.widget is not self.root or self.bg_image is None:
            return
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(60, self._update_window_bg)

    def _update_window_bg(self):
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if self.bg_image is None or w < 50 or h < 50:
            return
        self.bg_photo = ImageTk.PhotoImage(cover_dim(self.bg_image, (w, h), WINDOW_DIM))
        self.bg_label.config(image=self.bg_photo)

    def _render(self):
        c = self.viewer
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw < 40 or ch < 40:
            return
        if self.bg_image is not None:      # background image behind the view
            if self.viewer_bg_size != (cw, ch):
                self.viewer_bg_photo = ImageTk.PhotoImage(
                    cover_dim(self.bg_image, (cw, ch), VIEWER_DIM))
                self.viewer_bg_size = (cw, ch)
            c.create_image(0, 0, image=self.viewer_bg_photo, anchor="nw")
        if self.current_image is None:
            self._draw_placeholder(cw, ch)
            return
        iw, ih = self.current_image.size
        scale = min((cw - 24) / iw, (ch - 24) / ih)
        size = (max(int(iw * scale), 1), max(int(ih * scale), 1))
        self.photo = ImageTk.PhotoImage(self.current_image.resize(size, Image.Resampling.BILINEAR))
        c.create_image(cw // 2, ch // 2, image=self.photo)

    def _draw_placeholder(self, cw, ch):
        c, cx, cy = self.viewer, cw // 2, ch // 2
        c.create_polygon(rounded_points(cx - 270, cy - 130, cx + 270, cy + 72, 16),
                         smooth=True, fill=SURFACE, outline=BORDER)
        c.create_polygon(rounded_points(cx - 36, cy - 106, cx + 36, cy - 34, 14),
                         smooth=True, fill=SURFACE_2, outline=BORDER)
        c.create_line(cx + 4, cy - 96, cx - 6, cy - 78, cx + 8, cy - 68, cx - 4, cy - 44,
                      width=3, fill=ACCENT, joinstyle="round", capstyle="round")
        c.create_text(cx, cy, text="No source selected", fill=TEXT, font=(UI, 16, "bold"))
        c.create_text(cx, cy + 30, text="Open an image or video to run crack detection.",
                      fill=MUTED, font=(UI, 11))

    # actions -------------------------------------------------------------
    def select_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif")])
        if path:
            self._start(self._image_worker, path, os.path.basename(path), "Image")

    def select_video(self):
        path = filedialog.askopenfilename(
            filetypes=[("Video files", "*.mp4 *.avi *.mkv *.mov")])
        if path:
            self._start(self._video_worker, path, os.path.basename(path), "Video")

    def toggle_playback(self):
        """Pause / resume the video, or replay it once it has finished."""
        if not self.is_video or self.video_path is None:
            return
        if self.finished:
            self._start(self._video_worker, self.video_path,
                        os.path.basename(self.video_path), "Video")
            return
        if self.paused:
            self.paused = False
            self.play_event.set()
            self._set_status("Analyzing…", "busy")
        else:
            self.paused = True
            self.play_event.clear()
        self._refresh_buttons()

    def save_frame(self):
        if self.current_image is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".png",
                                            filetypes=[("PNG image", "*.png"), ("JPEG image", "*.jpg")])
        if path:
            self.current_image.save(path)

    def _start(self, worker, path, name, kind):
        if self.model is None:
            messagebox.showerror("Model not loaded",
                                 f"Could not load best.pt.\n\n{self.model_error or ''}")
            return
        self.stop_event.set()                 # stop any previous run
        self.play_event.set()                 # wake it if it was paused
        self.run_id += 1
        self.stop_event = threading.Event()
        self.play_event = threading.Event()
        self.play_event.set()
        self.running = True
        self.paused = False
        self.finished = False
        self.is_video = kind == "Video"
        self.video_path = path if self.is_video else None
        for tile in (self.tile_cracks, self.tile_conf, self.tile_frames, self.tile_ms):
            tile.set("–")
        self.source_text = f"{kind} · {name}"
        self._set_status("Analyzing…", "busy")
        self._refresh_buttons()
        threading.Thread(target=worker, args=(self.run_id, self.stop_event, self.play_event, path),
                         daemon=True).start()

    def _refresh_buttons(self):
        self.btn_play.set_enabled(self.is_video)
        if self.finished:
            self.btn_play.set_text("Replay video")
        elif self.paused:
            self.btn_play.set_text("Play video")
        else:
            self.btn_play.set_text("Pause video")
        self.btn_save.set_enabled(self.current_image is not None)
        self.source_label.config(
            text=self.source_text + ("  ·  Paused" if self.paused else ""))

    # background workers (inference never blocks the UI) -------------------
    def _analyze(self, run_id, frame, index):
        start = time.perf_counter()
        result = self.model.predict(source=frame, conf=self.conf, verbose=False)[0]
        ms = (time.perf_counter() - start) * 1000
        names = self.model.names
        confs = [float(b.conf) for b in result.boxes
                 if "crack" in str(names[int(b.cls)]).lower()]
        rgb = cv2.cvtColor(result.plot(font_size=9, line_width=2), cv2.COLOR_BGR2RGB)
        self.msgs.put({"type": "frame", "run": run_id, "image": Image.fromarray(rgb),
                       "cracks": len(confs), "top": max(confs) if confs else None,
                       "ms": ms, "index": index})

    def _image_worker(self, run_id, stop, play, path):
        try:
            frame = cv2.cvtColor(np.array(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
            self._analyze(run_id, frame, 1)
            self.msgs.put({"type": "done", "run": run_id, "video": False})
        except Exception as exc:
            self.msgs.put({"type": "error", "run": run_id, "text": str(exc)})

    def _video_worker(self, run_id, stop, play, path):
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self.msgs.put({"type": "error", "run": run_id, "text": "Could not open the video file."})
            return
        frame_time = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 30)
        index = 0
        try:
            while not stop.is_set():
                if not play.is_set():         # paused: wait without using CPU
                    play.wait(0.1)
                    continue
                tick = time.perf_counter()
                ok, frame = cap.read()
                if not ok:
                    break
                index += 1
                self._analyze(run_id, frame, index)
                wait = frame_time - (time.perf_counter() - tick)
                if wait > 0:
                    time.sleep(wait)
            self.msgs.put({"type": "done", "run": run_id, "video": True})
        except Exception as exc:
            self.msgs.put({"type": "error", "run": run_id, "text": str(exc)})
        finally:
            cap.release()

    # UI update loop ------------------------------------------------------
    def _poll(self):
        latest, others = None, []
        try:
            while True:
                msg = self.msgs.get_nowait()
                if msg["run"] != self.run_id:
                    continue                  # stale message from a stopped run
                if msg["type"] == "frame":
                    latest = msg              # only draw the newest frame
                else:
                    others.append(msg)
        except queue.Empty:
            pass

        if latest:
            self._show_frame(latest)
        for msg in others:
            if msg["type"] == "done":
                self.running = False
                if msg["video"]:
                    self.finished = True
                    self.paused = False
                    self._set_status("Video finished", "idle")
                self._refresh_buttons()
            elif msg["type"] == "error":
                self.running = False
                self.is_video = False
                self._refresh_buttons()
                self._set_status("Something went wrong", "idle")
                messagebox.showerror("Analysis failed", msg["text"])
        self.root.after(15, self._poll)

    def _show_frame(self, m):
        self.current_image = m["image"]
        self._render()
        found = m["cracks"] > 0
        self.tile_cracks.set(str(m["cracks"]), DANGER if found else OK)
        self.tile_conf.set(f"{m['top'] * 100:.0f}%" if m["top"] is not None else "–")
        self.tile_frames.set(str(m["index"]))
        self.tile_ms.set(f"{m['ms']:.0f} ms")
        self._set_status("Crack detected" if found else "No crack detected",
                         "crack" if found else "clear")
        self.btn_save.set_enabled(True)

    def _on_close(self):
        self.stop_event.set()
        self.root.destroy()


def main():
    global UI, SCALE
    if sys.platform.startswith("win"):
        try:  # crisp text on high-DPI Windows displays
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title("Crack Detection Model")
    root.geometry("1360x820")
    root.minsize(1080, 680)

    SCALE = max(root.winfo_fpixels("1i") / 96, 1.0)

    available = set(tkfont.families(root))
    for family in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Arial"):
        if family in available:
            UI = family
            break

    try:
        root.state("zoomed")
    except tk.TclError:
        pass

    CrackDetectionApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
