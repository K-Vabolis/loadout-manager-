"""
loadout_manager.py
-------------------
Standalone Loadout / Storage viewer.

- Define multiple "boxes" (storage areas: bag, bank, stash tab, etc.)
- Each box is a fixed grid of slots (rows x cols), positioned on screen
  from a single anchor point you click, plus slot size/spacing.
- Each slot shows an item image + the stacked quantity (OCR'd live from
  the screen every time you hit Capture).
- Per slot you can upload your own reference image; if you don't, the
  script just crops the icon out of the live screenshot itself.

Requires (pip install --user pillow pytesseract) and Tesseract-OCR
installed on Windows (https://github.com/UB-Mannheim/tesseract/wiki) with
its folder on PATH, or set TESSERACT_PATH below.

No memory reading, no file/process modification of the game -- this only
reads pixels off the screen.
"""

import ctypes
import json
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

try:
    from PIL import Image, ImageGrab, ImageTk, ImageOps
except ImportError:
    print("Missing dependency. Run:  pip install --user pillow pytesseract")
    sys.exit(1)

try:
    import pytesseract
    HAVE_OCR = True
except ImportError:
    HAVE_OCR = False

# If Tesseract isn't on PATH, point at it directly here, e.g.:
# TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_PATH = None
if HAVE_OCR and TESSERACT_PATH and os.path.exists(TESSERACT_PATH):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "loadout_config.json")
IMAGES_DIR = os.path.join(APP_DIR, "loadout_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# ── Theme ────────────────────────────────────────────────────────────────
BG_BLACK   = "#120d08"
BG_PANEL   = "#1d150d"
BROWN      = "#3b2a1a"
BROWN_LT   = "#5a4028"
GOLD       = "#d4af37"
GOLD_DIM   = "#8a723c"
CREAM      = "#f0e6d2"

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"boxes": []}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def slot_key(box_name, row, col):
    return f"{box_name}|{row}|{col}"


def compute_slot_rect(box, row, col):
    ax, ay = box["anchor"]
    w, h = box["slot_w"], box["slot_h"]
    px, py = box.get("pad_x", 4), box.get("pad_y", 4)
    x = ax + col * (w + px)
    y = ay + row * (h + py)
    return (x, y, x + w, y + h)


DEFAULT_COUNT_REL = (0.55, 0.62, 1.0, 1.0)  # left, top, right, bottom as fractions of slot


def ocr_count(img, rel=DEFAULT_COUNT_REL):
    """OCR the stack-count text out of a slot image. `rel` is the
    (left, top, right, bottom) crop box as fractions of the slot, calibrated
    per-box via 'Calibrate Count Region' since every game places the number
    in a different spot.

    Returns (count_str_or_None, error_str_or_None).
    """
    if not HAVE_OCR:
        return None, "no-ocr"
    w, h = img.size
    l, t, r, b = rel
    crop = img.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
    if crop.width < 2 or crop.height < 2:
        return None, "empty-crop"
    crop = crop.convert("L")
    crop = crop.resize((max(crop.width, 1) * 4, max(crop.height, 1) * 4), Image.LANCZOS)
    crop = ImageOps.autocontrast(crop)
    crop_bright = crop.point(lambda p: 255 if p > 150 else 0)
    crop_dark = crop.point(lambda p: 0 if p > 150 else 255)  # in case text is dark-on-light
    for variant in (crop_bright, crop_dark):
        try:
            text = pytesseract.image_to_string(
                variant, config="--psm 7 -c tessedit_char_whitelist=0123456789x"
            )
        except Exception as e:
            return None, f"ocr-error: {e}"
        digits = re.sub(r"[^0-9]", "", text)
        if digits:
            return digits, None
    return None, "no-digits-found"


class CalibrateCountDialog(tk.Toplevel):
    """Shows a zoomed screenshot of one real slot so you can drag a box
    around wherever the stack-count number actually appears. That region
    (as a fraction of the slot) is then used for OCR on every slot."""

    ZOOM = 6

    def __init__(self, master, slot_img, on_done):
        super().__init__(master)
        self.on_done = on_done
        self.title("Calibrate Count Region")
        self.configure(bg=BG_PANEL)
        self.resizable(False, False)

        self.slot_w, self.slot_h = slot_img.size
        zoomed = slot_img.resize(
            (self.slot_w * self.ZOOM, self.slot_h * self.ZOOM), Image.NEAREST
        )
        self._tkimg = ImageTk.PhotoImage(zoomed)

        tk.Label(self, text="Drag a box around the item COUNT number",
                 bg=BG_PANEL, fg=GOLD, font=("Consolas", 11, "bold")).pack(pady=(10, 4))

        self.canvas = tk.Canvas(self, width=zoomed.width, height=zoomed.height,
                                 highlightthickness=1, highlightbackground=GOLD_DIM,
                                 cursor="crosshair")
        self.canvas.pack(padx=10, pady=6)
        self.canvas.create_image(0, 0, anchor="nw", image=self._tkimg)

        self._start = None
        self._rect_id = None
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)

        tk.Button(self, text="Cancel", bg=BROWN_LT, fg=CREAM, relief="flat",
                  font=("Consolas", 10), command=self.destroy
                  ).pack(side="bottom", pady=(0, 10))

    def _start_drag(self, event):
        self._start = (event.x, event.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=GOLD, width=2
        )

    def _drag(self, event):
        if not self._start:
            return
        x0, y0 = self._start
        self.canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _end_drag(self, event):
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if right - left < 4 or bottom - top < 4:
            return
        rel = (left / (self.slot_w * self.ZOOM), top / (self.slot_h * self.ZOOM),
               right / (self.slot_w * self.ZOOM), bottom / (self.slot_h * self.ZOOM))
        self.destroy()
        self.on_done(rel)


class RectPicker(tk.Toplevel):
    """Full-screen transparent overlay -- click and drag to draw the box's
    outer rectangle. Slot size is then computed from however big you drag,
    divided across the rows/cols you set."""

    def __init__(self, master, on_pick, hint_text="Click and drag to size the storage box  (Esc to cancel)"):
        super().__init__(master)
        self.on_pick = on_pick
        self.attributes("-fullscreen", True)
        self.attributes("-alpha", 0.15)
        self.configure(bg="black", cursor="crosshair")
        self.attributes("-topmost", True)

        self._start = None
        self._rect_id = None

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self.hint = self.canvas.create_text(
            0, 0, text=hint_text,
            fill=GOLD, font=("Consolas", 14, "bold"), anchor="n"
        )
        self.after(10, self._place_hint)

        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

    def _place_hint(self):
        w = self.winfo_screenwidth()
        self.canvas.coords(self.hint, w / 2, 30)

    def _start_drag(self, event):
        self._start = (event.x, event.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=GOLD, width=2
        )

    def _drag(self, event):
        if not self._start or not self._rect_id:
            return
        x0, y0 = self._start
        self.canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _end_drag(self, event):
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        self.destroy()
        if right - left < 10 or bottom - top < 10:
            return  # ignore accidental clicks
        self.on_pick(left, top, right, bottom)


class GridSetupDialog(tk.Toplevel):
    """Just asks for a name -- rows, columns, slot size, and padding are all
    derived from two drags on screen (one slot, then the whole grid)."""

    def __init__(self, master, on_done, defaults=None):
        super().__init__(master)
        self.on_done = on_done
        self.title("Storage Box Setup")
        self.configure(bg=BG_PANEL)
        self.resizable(False, False)
        d = defaults or {}

        self.name_var = tk.StringVar(value=d.get("name", "New Box"))

        tk.Label(self, text="Box name", bg=BG_PANEL, fg=CREAM,
                 font=("Consolas", 10)).grid(row=0, column=0, sticky="w", padx=10, pady=(12, 4))
        tk.Entry(self, textvariable=self.name_var, bg=BROWN, fg=CREAM,
                  insertbackground=GOLD, relief="flat", width=22,
                  font=("Consolas", 10)).grid(row=0, column=1, padx=10, pady=(12, 4))

        tk.Label(self, text="Next you'll drag twice:\n"
                             "  1) around ONE slot, to set its exact size\n"
                             "  2) around the WHOLE grid, to fill it in",
                 bg=BG_PANEL, fg=GOLD_DIM, font=("Consolas", 9), justify="left"
                 ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 0))

        btn = tk.Button(self, text="Next: drag ONE slot  ->", bg=BROWN_LT, fg=GOLD,
                         relief="flat", font=("Consolas", 10, "bold"),
                         activebackground=GOLD, activeforeground="black",
                         command=self._confirm)
        btn.grid(row=2, column=0, columnspan=2, pady=(10, 12), ipadx=6, ipady=4)

    def _confirm(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid input", "Box needs a name.")
            return
        self.destroy()
        self.on_done({"name": name})


class SlotWidget(tk.Frame):
    SIZE = 72  # on-screen thumbnail size, independent of actual capture size

    def __init__(self, master, app, box_name, row, col):
        super().__init__(master, bg=BROWN, width=self.SIZE, height=self.SIZE,
                          highlightbackground=GOLD_DIM, highlightthickness=1)
        self.app = app
        self.box_name = box_name
        self.row = row
        self.col = col
        self.grid_propagate(False)

        self.img_label = tk.Label(self, bg=BROWN)
        self.img_label.place(relx=0.5, rely=0.5, anchor="center")

        self.count_var = tk.StringVar(value="")
        self.count_label = tk.Label(self, textvariable=self.count_var, bg="black", fg=GOLD,
                                     font=("Consolas", 8, "bold"))
        self.count_label.place(relx=1.0, rely=1.0, anchor="se")

        self.bind("<Button-3>", self._menu)
        self.img_label.bind("<Button-3>", self._menu)
        self._tkimg = None

    def _menu(self, event):
        m = tk.Menu(self, tearoff=0, bg=BROWN, fg=CREAM, activebackground=GOLD,
                    activeforeground="black")
        m.add_command(label="Upload image for this slot", command=self._upload)
        m.add_command(label="Clear uploaded image", command=self._clear_custom)
        m.tk_popup(event.x_root, event.y_root)

    def _upload(self):
        path = filedialog.askopenfilename(
            title="Choose item image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp")]
        )
        if not path:
            return
        dest = os.path.join(IMAGES_DIR, f"{slot_key(self.box_name, self.row, self.col)}.png")
        try:
            Image.open(path).convert("RGBA").save(dest)
        except Exception as e:
            messagebox.showerror("Upload failed", str(e))
            return
        self.app.render_slot_image(self.box_name, self.row, self.col, from_file=dest)

    def _clear_custom(self):
        dest = os.path.join(IMAGES_DIR, f"{slot_key(self.box_name, self.row, self.col)}.png")
        if os.path.exists(dest):
            os.remove(dest)
        self.app.status(f"Cleared custom image for slot ({self.row},{self.col}) -- "
                         f"will auto-capture next time.")

    def set_image(self, pil_img):
        thumb = pil_img.convert("RGBA").resize((self.SIZE - 6, self.SIZE - 6), Image.LANCZOS)
        self._tkimg = ImageTk.PhotoImage(thumb)
        self.img_label.configure(image=self._tkimg)

    def set_count(self, text, ok=True):
        self.count_var.set(text or "")
        self.count_label.configure(fg=GOLD if ok else "#e08080")


class LoadoutApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Loadout Manager")
        self.configure(bg=BG_BLACK)
        self.geometry("880x560")
        self.minsize(700, 460)

        self.cfg = load_config()
        self.active_box = None
        self.slot_widgets = {}  # box_name -> {(r,c): SlotWidget}

        self._build_ui()
        self._refresh_box_list()
        if self.cfg["boxes"]:
            self._select_box(self.cfg["boxes"][0]["name"])

        if not HAVE_OCR:
            self.after(300, lambda: messagebox.showwarning(
                "OCR unavailable",
                "pytesseract / Tesseract-OCR not found -- counts will show as '?'.\n"
                "Install with: pip install --user pytesseract\n"
                "and https://github.com/UB-Mannheim/tesseract/wiki"
            ))

    # ── UI scaffolding ──────────────────────────────────────────────
    def _build_ui(self):
        top = tk.Frame(self, bg=BG_BLACK)
        top.pack(fill="x", side="top", padx=12, pady=(10, 4))
        tk.Label(top, text="LOADOUT MANAGER", bg=BG_BLACK, fg=GOLD,
                 font=("Consolas", 16, "bold")).pack(side="left")

        body = tk.Frame(self, bg=BG_BLACK)
        body.pack(fill="both", expand=True, padx=12, pady=8)

        # Sidebar
        sidebar = tk.Frame(body, bg=BG_PANEL, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="STORAGE BOXES", bg=BG_PANEL, fg=GOLD_DIM,
                 font=("Consolas", 9, "bold")).pack(pady=(10, 4))
        self.box_list = tk.Listbox(sidebar, bg=BROWN, fg=CREAM, selectbackground=GOLD,
                                    selectforeground="black", relief="flat",
                                    font=("Consolas", 11), highlightthickness=0,
                                    activestyle="none")
        self.box_list.pack(fill="both", expand=True, padx=8, pady=4)
        self.box_list.bind("<<ListboxSelect>>", self._on_box_select)

        tk.Button(sidebar, text="+ Add Storage Box", bg=BROWN_LT, fg=GOLD, relief="flat",
                  font=("Consolas", 10, "bold"), activebackground=GOLD,
                  activeforeground="black", command=self._add_box
                  ).pack(fill="x", padx=8, pady=(4, 10), ipady=4)

        # Main panel
        main = tk.Frame(body, bg=BG_BLACK)
        main.pack(side="left", fill="both", expand=True, padx=(12, 0))

        actions = tk.Frame(main, bg=BG_BLACK)
        actions.pack(fill="x", pady=(0, 8))
        self.box_title_var = tk.StringVar(value="No box selected")
        tk.Label(actions, textvariable=self.box_title_var, bg=BG_BLACK, fg=CREAM,
                 font=("Consolas", 13, "bold")).pack(side="left")

        tk.Button(actions, text="Capture Now", bg=GOLD, fg="black", relief="flat",
                  font=("Consolas", 10, "bold"), activebackground=CREAM,
                  command=self._capture_active_box).pack(side="right", padx=4, ipadx=6, ipady=3)
        tk.Button(actions, text="Resize Box", bg=BROWN_LT, fg=GOLD, relief="flat",
                  font=("Consolas", 10), activebackground=GOLD, activeforeground="black",
                  command=self._resize_box).pack(side="right", padx=4, ipadx=6, ipady=3)
        tk.Button(actions, text="Calibrate Count", bg=BROWN_LT, fg=GOLD, relief="flat",
                  font=("Consolas", 10), activebackground=GOLD, activeforeground="black",
                  command=self._calibrate_count).pack(side="right", padx=4, ipadx=6, ipady=3)
        tk.Button(actions, text="Delete Box", bg=BROWN_LT, fg="#e08080", relief="flat",
                  font=("Consolas", 10), activebackground="#e08080", activeforeground="black",
                  command=self._delete_box).pack(side="right", padx=4, ipadx=6, ipady=3)

        self.grid_frame = tk.Frame(main, bg=BG_BLACK)
        self.grid_frame.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Right-click a slot to upload a custom image.")
        tk.Label(self, textvariable=self.status_var, bg=BG_BLACK, fg=GOLD_DIM,
                 font=("Consolas", 9)).pack(side="bottom", fill="x", padx=12, pady=(0, 8))

    def status(self, text):
        self.status_var.set(text)

    # ── Box management ──────────────────────────────────────────────
    def _refresh_box_list(self):
        self.box_list.delete(0, "end")
        for b in self.cfg["boxes"]:
            self.box_list.insert("end", b["name"])

    def _find_box(self, name):
        for b in self.cfg["boxes"]:
            if b["name"] == name:
                return b
        return None

    def _add_box(self):
        GridSetupDialog(self, self._on_grid_setup_done)

    def _on_grid_setup_done(self, cfg):
        existing = self._find_box(cfg["name"])
        if existing and cfg["name"] != getattr(self, "_editing_box", None):
            messagebox.showerror("Name in use", "A box with that name already exists.")
            return

        def finish(box):
            if not existing:
                self.cfg["boxes"].append(box)
            save_config(self.cfg)
            self._refresh_box_list()
            self._select_box(box["name"])
            self.status(f"'{box['name']}' -- {box['rows']} rows x {box['cols']} cols, "
                        f"{box['slot_w']}x{box['slot_h']} per slot.")

        box = existing or {}
        box.update(cfg)
        self._pick_slot_then_grid(box, finish)

    def _pick_slot_then_grid(self, box, on_finish):
        """First drag = exactly one slot (sets slot_w/slot_h/anchor).
        Second drag = the whole grid area (used to derive rows/cols/padding)."""

        def slot_picked(x0, y0, x1, y1):
            box["anchor"] = [x0, y0]
            box["slot_w"] = x1 - x0
            box["slot_h"] = y1 - y0
            self.status("Now drag around the WHOLE grid of slots...")
            RectPicker(self, grid_picked,
                       hint_text="Drag around the WHOLE grid (all rows/cols)  (Esc to cancel)")

        def grid_picked(gx0, gy0, gx1, gy1):
            self._apply_grid(box, gx0, gy0, gx1, gy1)
            on_finish(box)

        self.status("Drag a rectangle around exactly ONE slot...")
        RectPicker(self, slot_picked,
                   hint_text="Drag around exactly ONE slot  (Esc to cancel)")

    def _apply_grid(self, box, gx0, gy0, gx1, gy1):
        """Given the whole-grid rectangle and the already-known slot size,
        work out how many rows/cols fit and space them evenly."""
        slot_w, slot_h = box["slot_w"], box["slot_h"]
        ax, ay = box["anchor"]
        total_w = max(gx1 - ax, slot_w)
        total_h = max(gy1 - ay, slot_h)

        cols = max(1, round(total_w / slot_w))
        rows = max(1, round(total_h / slot_h))

        pad_x = int((total_w - cols * slot_w) / (cols - 1)) if cols > 1 else 0
        pad_y = int((total_h - rows * slot_h) / (rows - 1)) if rows > 1 else 0

        box["rows"] = rows
        box["cols"] = cols
        box["pad_x"] = max(0, pad_x)
        box["pad_y"] = max(0, pad_y)

    def _resize_box(self):
        if not self.active_box:
            return
        box = self._find_box(self.active_box)

        def finish(_box):
            save_config(self.cfg)
            self._build_grid(box)
            self.status(f"Resized -- {box['rows']} rows x {box['cols']} cols, "
                        f"{box['slot_w']}x{box['slot_h']} per slot.")

        self._pick_slot_then_grid(box, finish)

    def _delete_box(self):
        if not self.active_box:
            return
        if not messagebox.askyesno("Delete box", f"Delete '{self.active_box}'?"):
            return
        self.cfg["boxes"] = [b for b in self.cfg["boxes"] if b["name"] != self.active_box]
        save_config(self.cfg)
        self.active_box = None
        self._refresh_box_list()
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.box_title_var.set("No box selected")

    def _on_box_select(self, event):
        sel = self.box_list.curselection()
        if not sel:
            return
        name = self.box_list.get(sel[0])
        self._select_box(name)

    def _select_box(self, name):
        self.active_box = name
        self.box_title_var.set(name)
        box = self._find_box(name)
        self._build_grid(box)

    # ── Grid rendering ──────────────────────────────────────────────
    def _build_grid(self, box):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        widgets = {}
        for r in range(box["rows"]):
            for c in range(box["cols"]):
                w = SlotWidget(self.grid_frame, self, box["name"], r, c)
                w.grid(row=r, column=c, padx=3, pady=3)
                widgets[(r, c)] = w
                custom = os.path.join(IMAGES_DIR, f"{slot_key(box['name'], r, c)}.png")
                if os.path.exists(custom):
                    try:
                        w.set_image(Image.open(custom))
                    except Exception:
                        pass
        self.slot_widgets[box["name"]] = widgets

    def render_slot_image(self, box_name, row, col, from_file=None, from_pil=None):
        widgets = self.slot_widgets.get(box_name, {})
        w = widgets.get((row, col))
        if not w:
            return
        if from_file:
            w.set_image(Image.open(from_file))
        elif from_pil is not None:
            w.set_image(from_pil)

    def _calibrate_count(self):
        if not self.active_box:
            return
        if not HAVE_OCR:
            messagebox.showwarning(
                "OCR unavailable",
                "pytesseract / Tesseract-OCR isn't installed, so counts can't be read.\n"
                "Install with: pip install --user pytesseract\n"
                "and https://github.com/UB-Mannheim/tesseract/wiki"
            )
            return
        box = self._find_box(self.active_box)
        rect = compute_slot_rect(box, 0, 0)
        try:
            sample = ImageGrab.grab(bbox=rect)
        except Exception as e:
            messagebox.showerror("Capture failed", str(e))
            return

        def done(rel):
            box["count_rel"] = list(rel)
            save_config(self.cfg)
            self.status(f"Count region calibrated for '{box['name']}'. Hit Capture to test it.")

        CalibrateCountDialog(self, sample, done)

    # ── Capture / OCR ────────────────────────────────────────────────
    def _capture_active_box(self):
        if not self.active_box:
            return
        box = self._find_box(self.active_box)
        rel = box.get("count_rel", list(DEFAULT_COUNT_REL))
        self.status("Capturing...")
        self.update()

        read_ok = 0
        total = box["rows"] * box["cols"]
        last_err = None

        try:
            for r in range(box["rows"]):
                for c in range(box["cols"]):
                    rect = compute_slot_rect(box, r, c)
                    shot = ImageGrab.grab(bbox=rect)

                    custom_path = os.path.join(
                        IMAGES_DIR, f"{slot_key(box['name'], r, c)}.png"
                    )
                    if os.path.exists(custom_path):
                        display_img = Image.open(custom_path)
                    else:
                        display_img = shot  # auto-captured icon

                    count, err = ocr_count(shot, rel)
                    if count:
                        read_ok += 1
                    else:
                        last_err = err

                    widgets = self.slot_widgets.get(box["name"], {})
                    w = widgets.get((r, c))
                    if w:
                        w.set_image(display_img)
                        if count:
                            w.set_count(count, ok=True)
                        elif not HAVE_OCR:
                            w.set_count("N/A", ok=False)
                        else:
                            w.set_count("--", ok=False)

            if not HAVE_OCR:
                self.status("Captured, but Tesseract-OCR isn't installed -- counts show N/A.")
            elif read_ok == 0:
                self.status(
                    f"Captured, but 0/{total} slots had a readable number "
                    f"(last reason: {last_err}). Try 'Calibrate Count' to point at the "
                    f"exact digits."
                )
            else:
                self.status(f"Captured -- {read_ok}/{total} slots read a count.")
        except Exception as e:
            self.status(f"Capture error: {e}")


if __name__ == "__main__":
    try:
        app = LoadoutApp()
        app.mainloop()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close...")
