"""
Offline batch background remover.

Install once (needs internet):
    pip install "rembg[cpu]" pillow
    (NVIDIA GPU users: pip install "rembg[gpu]" pillow)

Run:
    python bg_remover.py

Pick an input folder and an output folder, choose a mode, click Start.
Already-processed files are skipped, so you can stop and resume any time.
"""
import os
import sys

# When packaged as an .exe, use the models bundled inside it (fully offline)
if getattr(sys, "frozen", False):
    os.environ["U2NET_HOME"] = os.path.join(sys._MEIPASS, "models")
    # windowed apps have no console; avoid crashes from libraries that print
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps
from rembg import new_session, remove

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MODELS = ["u2net", "isnet-general-use", "u2netp"]


def collect_files(src: Path, recursive: bool):
    it = src.rglob("*") if recursive else src.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in EXTS)


def output_path(f: Path, src: Path, dst: Path, mode: str, used: set):
    rel = f.relative_to(src)
    if mode == "transparent":  # transparency needs PNG
        out = dst / rel.with_suffix(".png")
        if out in used:  # a.jpg and a.png in same folder
            out = dst / rel.with_name(f"{f.stem}_{f.suffix[1:].lower()}.png")
    else:  # white background -> keep original extension
        out = dst / rel
    used.add(out)
    return out


def process_one(f: Path, out: Path, session, mode: str):
    img = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
    cut = remove(img, session=session)  # RGBA
    out.parent.mkdir(parents=True, exist_ok=True)
    if mode == "transparent":
        cut.save(out, "PNG")
    else:
        bg = Image.new("RGB", cut.size, (255, 255, 255))
        bg.paste(cut, mask=cut.split()[3])
        if out.suffix.lower() in (".jpg", ".jpeg"):
            bg.save(out, quality=95)
        else:
            bg.save(out)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Offline Background Remover")
        self.geometry("620x330")
        self.stop_flag = False
        self.src = tk.StringVar()
        self.dst = tk.StringVar()
        self.mode = tk.StringVar(value="transparent")
        self.model = tk.StringVar(value=MODELS[0])
        self.recursive = tk.BooleanVar(value=True)
        self.skip = tk.BooleanVar(value=True)

        pad = {"padx": 8, "pady": 5}
        for i, (label, var) in enumerate([("Input folder", self.src), ("Output folder", self.dst)]):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", **pad)
            ttk.Entry(self, textvariable=var, width=55).grid(row=i, column=1, **pad)
            ttk.Button(self, text="Browse", command=lambda v=var: v.set(filedialog.askdirectory() or v.get())).grid(row=i, column=2, **pad)

        ttk.Label(self, text="Output").grid(row=2, column=0, sticky="w", **pad)
        box = ttk.Frame(self)
        box.grid(row=2, column=1, sticky="w")
        ttk.Radiobutton(box, text="Transparent PNG (same name, .png)", variable=self.mode, value="transparent").pack(anchor="w")
        ttk.Radiobutton(box, text="White background (keeps original extension)", variable=self.mode, value="white").pack(anchor="w")

        ttk.Label(self, text="Model").grid(row=3, column=0, sticky="w", **pad)
        ttk.Combobox(self, textvariable=self.model, values=MODELS, state="readonly", width=22).grid(row=3, column=1, sticky="w", **pad)

        ttk.Checkbutton(self, text="Include sub-folders", variable=self.recursive).grid(row=4, column=1, sticky="w", **pad)
        ttk.Checkbutton(self, text="Skip files already in output (resume)", variable=self.skip).grid(row=5, column=1, sticky="w", **pad)

        self.bar = ttk.Progressbar(self, length=420)
        self.bar.grid(row=6, column=0, columnspan=2, **pad)
        self.status = ttk.Label(self, text="Ready")
        self.status.grid(row=7, column=0, columnspan=3, sticky="w", **pad)
        self.start_btn = ttk.Button(self, text="Start", command=self.start)
        self.start_btn.grid(row=6, column=2, **pad)
        ttk.Button(self, text="Stop", command=lambda: setattr(self, "stop_flag", True)).grid(row=7, column=2, **pad)

    def start(self):
        if not self.src.get() or not self.dst.get():
            messagebox.showwarning("Missing folder", "Choose both input and output folders.")
            return
        if Path(self.src.get()).resolve() == Path(self.dst.get()).resolve():
            messagebox.showwarning("Same folder", "Output folder must be different from input (originals would be overwritten).")
            return
        self.stop_flag = False
        self.start_btn.state(["disabled"])
        threading.Thread(target=self.run, daemon=True).start()

    def set_status(self, text, value=None):
        self.after(0, lambda: (self.status.config(text=text), value is not None and self.bar.config(value=value)))

    def run(self):
        src, dst = Path(self.src.get()), Path(self.dst.get())
        mode = self.mode.get()
        self.set_status("Loading model (first run downloads it once)...")
        try:
            session = new_session(self.model.get())
        except Exception as e:
            self.set_status(f"Model error: {e}")
            self.after(0, lambda: self.start_btn.state(["!disabled"]))
            return

        files = collect_files(src, self.recursive.get())
        total, done, failed, used = len(files), 0, [], set()
        self.after(0, lambda: self.bar.config(maximum=max(total, 1)))
        t0 = time.time()

        for f in files:
            if self.stop_flag:
                break
            out = output_path(f, src, dst, mode, used)
            if self.skip.get() and out.exists():
                done += 1
                self.set_status(f"Skipped {done}/{total}", done)
                continue
            try:
                process_one(f, out, session, mode)
            except Exception as e:
                failed.append(f"{f}\t{e}")
            done += 1
            rate = (time.time() - t0) / max(done, 1)
            self.set_status(f"{done}/{total}  |  failed: {len(failed)}  |  ~{int(rate * (total - done) / 60)} min left", done)

        if failed:
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "errors.txt").write_text("\n".join(failed), encoding="utf-8")
        self.set_status(("Stopped" if self.stop_flag else "Finished") + f": {done}/{total}, failed: {len(failed)}")
        self.after(0, lambda: self.start_btn.state(["!disabled"]))


if __name__ == "__main__":
    App().mainloop()
