# Music Deduplicator — AI-generated (OpenCode / DeepSeek)
# https://github.com/velosipedf-cell/music-deduplicator

import os
import re
import json
import struct
import hashlib
import shutil
import threading
import tempfile
from pathlib import Path
from collections import defaultdict

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import scipy.signal
import miniaudio

from mutagen.mp3 import MP3
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError


# ──────────────────────── constants ─────────────────────────

SAMPLE_RATE = 22050
SKIP_SECONDS = 10
ANALYZE_SECONDS = 80
SIMILARITY_THRESHOLD = 0.80


# ──────────────────────── tag helpers ───────────────────────

def normalize(s):
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(
        r'\s*[\[\(]\s*(?:official|lyrics?|hd|hq|audio|video|music|clip|'
        r'prod\.?|feat\.?|ft\.?|remastered|remix|edit|bonus|live|demo|'
        r'instrumental|acoustic|clean|dirty|explicit|radio.?edit|extended)\b'
        r'[^\]\)]*[\]\)]',
        '', s, flags=re.IGNORECASE,
    )
    s = re.sub(r'\s*[\[\(]\d+[\]\)]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def read_tags(filepath):
    try:
        tags = EasyID3(filepath)
    except ID3NoHeaderError:
        return None, None
    artist = tags.get('artist', [None])[0]
    title = tags.get('title', [None])[0]
    return artist, title


def get_bitrate(filepath):
    try:
        audio = MP3(filepath)
        if audio.info and audio.info.bitrate:
            return audio.info.bitrate // 1000
    except Exception:
        pass
    return 0


# ──────────────────────── fingerprint ───────────────────────

class FingerprintCache:
    def __init__(self):
        self._cache_dir = Path(tempfile.gettempdir()) / "music_dedup_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory = {}

    def _key(self, filepath):
        path_str = str(filepath)
        mtime = filepath.stat().st_mtime_ns
        size = filepath.stat().st_size
        raw = f"{path_str}|{mtime}|{size}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, filepath):
        k = self._key(filepath)
        if k in self._memory:
            return self._memory[k]
        cache_file = self._cache_dir / f"{k}.fp"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                arr = np.array(data["bits"], dtype=np.uint8)
                self._memory[k] = arr
                return arr
            except Exception:
                pass
        return None

    def put(self, filepath, fp_array):
        k = self._key(filepath)
        self._memory[k] = fp_array
        cache_file = self._cache_dir / f"{k}.fp"
        try:
            cache_file.write_text(json.dumps({"bits": fp_array.tolist()}))
        except Exception:
            pass


def _decode_audio(filepath):
    try:
        audio = miniaudio.decode_file(str(filepath), sample_rate=SAMPLE_RATE)
        samples = np.array(audio.samples, dtype=np.float32)
        if audio.nchannels > 1:
            samples = samples.reshape(-1, audio.nchannels).mean(axis=1)
    except Exception:
        return None
    return samples


def compute_fingerprint(filepath):
    samples = _decode_audio(filepath)
    if samples is None or len(samples) < SAMPLE_RATE * 3:
        return None

    total_samples = len(samples)
    skip_samples = int(SKIP_SECONDS * SAMPLE_RATE)
    take_samples = int(ANALYZE_SECONDS * SAMPLE_RATE)

    if skip_samples >= total_samples:
        segment = samples
    else:
        start = min(skip_samples, total_samples)
        end = min(start + take_samples, total_samples)
        segment = samples[start:end]

    if len(segment) < SAMPLE_RATE * 3:
        segment = samples[:min(take_samples, total_samples)]

    nperseg = 4096
    noverlap = nperseg // 2

    f, t, Sxx = scipy.signal.spectrogram(
        segment, fs=SAMPLE_RATE,
        nperseg=nperseg, noverlap=noverlap,
        window='hann', mode='magnitude',
    )

    Sxx = np.abs(Sxx)

    mel_bands = 32
    min_freq = 200
    max_freq = SAMPLE_RATE // 2
    mel_edges = np.linspace(
        np.log10(min_freq), np.log10(max_freq), mel_bands + 1
    )
    mel_edges = 10 ** mel_edges

    mel_spec = np.zeros((mel_bands, len(t)), dtype=np.float32)
    for i in range(mel_bands):
        lo = mel_edges[i]
        hi = mel_edges[i + 1]
        mask = (f >= lo) & (f < hi)
        if mask.any():
            mel_spec[i] = Sxx[mask].sum(axis=0)

    mel_spec = np.log1p(mel_spec)

    fingerprint = np.zeros((mel_bands, len(t) - 1), dtype=np.uint8)
    for b in range(mel_bands):
        diff = np.diff(mel_spec[b])
        fingerprint[b] = (diff > 0).astype(np.uint8)

    return fingerprint.flatten()


def compare_fingerprints(fp1, fp2):
    if fp1 is None or fp2 is None:
        return 0.0
    length = min(len(fp1), len(fp2))
    a = fp1[:length]
    b = fp2[:length]
    same = (a == b).sum()
    return float(same) / length


# ──────────────────────── model ─────────────────────────────

class Group:
    def __init__(self, artist, title, best, duplicates, match_type="tags"):
        self.artist = artist
        self.title = title
        self.best = best
        self.duplicates = duplicates
        self.match_type = match_type


class FileEntry:
    def __init__(self, path, bitrate, is_best):
        self.path = path
        self.bitrate = bitrate
        self.is_best = is_best
        self.similarity = 0.0
        self.selected = not is_best


class DedupModel:
    def __init__(self):
        self.groups = []
        self.fp_cache = FingerprintCache()

    # ── tags mode ───────────────────────────────────────────

    def scan_tags(self, root_dir, progress_callback=None):
        mp3_files = list(Path(root_dir).rglob('*.mp3'))
        total = len(mp3_files)
        groups_map = defaultdict(list)

        for idx, fp in enumerate(mp3_files):
            if progress_callback:
                progress_callback(idx, total, fp.name)

            artist, title = read_tags(fp)
            artist_n = normalize(artist or '')
            title_n = normalize(title or '')

            if not artist_n or not title_n:
                stem = fp.stem
                parts = stem.rsplit(' - ', 1)
                if len(parts) == 2:
                    artist_n = normalize(parts[0]) if not artist_n else artist_n
                    title_n = normalize(parts[1]) if not title_n else title_n

            key = (artist_n, title_n) if artist_n and title_n else fp.stem.lower()
            bitrate = get_bitrate(fp)
            groups_map[key].append((fp, bitrate, artist_n, title_n))

        self._build_groups(groups_map, "tags")
        if progress_callback:
            progress_callback(total, total, "")

    # ── acoustic mode ───────────────────────────────────────

    def scan_acoustic(self, root_dir, progress_callback=None):
        mp3_files = list(Path(root_dir).rglob('*.mp3'))
        fps = {}
        total = len(mp3_files)

        for idx, fp in enumerate(mp3_files):
            if progress_callback:
                progress_callback(idx, total, f"[fp] {fp.name}")

            cached = self.fp_cache.get(fp)
            if cached is not None:
                fps[fp] = cached
            else:
                fprint = compute_fingerprint(fp)
                if fprint is not None:
                    self.fp_cache.put(fp, fprint)
                    fps[fp] = fprint

        if progress_callback:
            progress_callback(0, len(fps), "Comparing fingerprints…")

        threshold = SIMILARITY_THRESHOLD
        groups_map = defaultdict(list)
        used = set()

        items = list(fps.items())

        for i in range(len(items)):
            fp_a, fprint_a = items[i]
            if fp_a in used:
                continue

            key = f"acoustic_{i}"
            bitrate = get_bitrate(fp_a)
            artist, title = read_tags(fp_a)
            artist_n = normalize(artist or '') or str(fp_a.parent.name)
            title_n = normalize(title or '') or fp_a.stem

            if progress_callback and i % 10 == 0:
                progress_callback(i, len(items), f"[compare] {fp_a.name}")

            groups_map[key].append((fp_a, bitrate, artist_n, title_n))
            used.add(fp_a)

            for j in range(i + 1, len(items)):
                fp_b = items[j][0]
                if fp_b in used:
                    continue
                sim = compare_fingerprints(fprint_a, items[j][1])
                if sim >= threshold:
                    br = get_bitrate(fp_b)
                    ar, ti = read_tags(fp_b)
                    ar_n = normalize(ar or '') or str(fp_b.parent.name)
                    ti_n = normalize(ti or '') or fp_b.stem
                    groups_map[key].append((fp_b, br, ar_n, ti_n))
                    used.add(fp_b)

        self._build_groups(groups_map, "acoustic")
        if progress_callback:
            progress_callback(total, total, "")

    # ── hybrid mode (tags → acoustic verify) ─────────────────

    def scan_hybrid(self, root_dir, progress_callback=None):
        mp3_files = list(Path(root_dir).rglob('*.mp3'))
        total = len(mp3_files)
        groups_map = defaultdict(list)

        for idx, fp in enumerate(mp3_files):
            if progress_callback:
                progress_callback(idx, total, fp.name)

            artist, title = read_tags(fp)
            artist_n = normalize(artist or '')
            title_n = normalize(title or '')

            if not artist_n or not title_n:
                stem = fp.stem
                parts = stem.rsplit(' - ', 1)
                if len(parts) == 2:
                    artist_n = normalize(parts[0]) if not artist_n else artist_n
                    title_n = normalize(parts[1]) if not title_n else title_n

            key = (artist_n, title_n) if artist_n and title_n else fp.stem.lower()
            bitrate = get_bitrate(fp)
            groups_map[key].append((fp, bitrate, artist_n, title_n))

        self._build_groups(groups_map, "hybrid")

        if progress_callback:
            progress_callback(0, len(self.groups), "Acoustic verification…")

        verified_groups = []
        for gidx, group in enumerate(self.groups):
            if len(group.duplicates) + 1 < 2:
                continue
            if progress_callback:
                progress_callback(gidx, len(self.groups), f"[verify] {group.artist} — {group.title}")

            best_fp = None
            try:
                cached = self.fp_cache.get(group.best.path)
                if cached is not None:
                    best_fp = cached
                else:
                    best_fp = compute_fingerprint(group.best.path)
                    if best_fp is not None:
                        self.fp_cache.put(group.best.path, best_fp)
            except Exception:
                pass

            if best_fp is None:
                verified_groups.append(group)
                continue

            verified_dups = []
            for dup in group.duplicates:
                try:
                    dup_fp = self.fp_cache.get(dup.path)
                    if dup_fp is None:
                        dup_fp = compute_fingerprint(dup.path)
                        if dup_fp is not None:
                            self.fp_cache.put(dup.path, dup_fp)
                    if dup_fp is not None:
                        sim = compare_fingerprints(best_fp, dup_fp)
                        dup.similarity = sim
                        if sim >= SIMILARITY_THRESHOLD:
                            verified_dups.append(dup)
                except Exception:
                    verified_dups.append(dup)

            if verified_dups:
                group.duplicates = verified_dups
                verified_groups.append(group)

        self.groups = verified_groups

        if progress_callback:
            progress_callback(total, total, "")

    # ── common ──────────────────────────────────────────────

    def _build_groups(self, groups_map, match_type):
        self.groups = []
        for key, files in groups_map.items():
            if len(files) < 2:
                continue
            files.sort(key=lambda x: x[1], reverse=True)
            best = FileEntry(files[0][0], files[0][1], True)
            dups = []
            for f in files[1:]:
                e = FileEntry(f[0], f[1], False)
                dups.append(e)
            artist_display = files[0][2] or "?"
            title_display = files[0][3] or "?"
            self.groups.append(Group(artist_display, title_display, best, dups, match_type))


# ──────────────────────── gui ──────────────────────────────

class DedupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Music Deduplicator")
        self.root.geometry("960x700")
        self.root.minsize(750, 450)
        self.model = DedupModel()
        self._group_widgets = []
        self._cancel_flag = False
        self._build_ui()

    def _build_ui(self):
        # ── top bar ──
        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="Folder:").pack(side=tk.LEFT)

        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(top_frame, textvariable=self.folder_var, width=55)
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))

        ttk.Button(top_frame, text="Browse…", command=self._browse_folder).pack(side=tk.LEFT)

        # ── mode selector ──
        mode_frame = ttk.LabelFrame(self.root, text="Scan mode", padding=8)
        mode_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        self.mode_var = tk.StringVar(value="hybrid")
        ttk.Radiobutton(mode_frame, text="Tags only — compare by ID3 tags (artist + title)",
                        variable=self.mode_var, value="tags").pack(anchor=tk.W)
        ttk.Radiobutton(mode_frame, text="Acoustic — compare by audio content (fingerprint)",
                        variable=self.mode_var, value="acoustic").pack(anchor=tk.W)
        ttk.Radiobutton(mode_frame, text="Hybrid — tags first, then acoustic verification (recommended)",
                        variable=self.mode_var, value="hybrid").pack(anchor=tk.W)

        # ── scan button row ──
        scan_frame = ttk.Frame(self.root, padding=(10, 0, 10, 5))
        scan_frame.pack(fill=tk.X)
        self.scan_btn = ttk.Button(scan_frame, text="▶  Scan", command=self._start_scan)
        self.scan_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(scan_frame, text="Cancel", command=self._cancel_scan, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(10, 0))

        # ── progress ──
        progress_frame = ttk.Frame(self.root, padding=(10, 0, 10, 5))
        progress_frame.pack(fill=tk.X)
        self.progress = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="Ready — choose a folder and click Scan")
        self.status_label = ttk.Label(progress_frame, textvariable=self.status_var, foreground="gray")
        self.status_label.pack(anchor=tk.W, pady=(2, 0))

        # ── results area ──
        res_frame = ttk.Frame(self.root, padding=(10, 5, 10, 5))
        res_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(res_frame, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(res_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.results_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.results_frame, anchor=tk.NW, tags="results"
        )

        self.results_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # ── bottom bar ──
        bottom_frame = ttk.Frame(self.root, padding=10)
        bottom_frame.pack(fill=tk.X)

        self.select_all_var = tk.BooleanVar(value=True)
        self.select_all_cb = ttk.Checkbutton(
            bottom_frame, text="Select / deselect all",
            variable=self.select_all_var, command=self._toggle_all
        )
        self.select_all_cb.pack(side=tk.LEFT)
        self.select_all_cb.configure(state=tk.DISABLED)

        self.action_var = tk.StringVar(value="delete")
        ttk.Radiobutton(bottom_frame, text="Delete", variable=self.action_var,
                        value="delete").pack(side=tk.LEFT, padx=(20, 0))
        ttk.Radiobutton(bottom_frame, text="Move to _duplicates", variable=self.action_var,
                        value="move").pack(side=tk.LEFT, padx=10)

        self.execute_btn = ttk.Button(bottom_frame, text="Execute", command=self._execute)
        self.execute_btn.pack(side=tk.RIGHT)
        self.execute_btn.configure(state=tk.DISABLED)

        ttk.Button(bottom_frame, text="Clear", command=self._clear_results).pack(
            side=tk.RIGHT, padx=(0, 10))

        self.summary_var = tk.StringVar(value="")
        self.summary_label = ttk.Label(
            bottom_frame, textvariable=self.summary_var, foreground="gray")
        self.summary_label.pack(side=tk.RIGHT, padx=20)

    # ── scroll helpers ─────────────────────────────────────

    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── actions ────────────────────────────────────────────

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select music folder")
        if folder:
            self.folder_var.set(folder)

    def _cancel_scan(self):
        self._cancel_flag = True
        self.status_var.set("Cancelling…")

    def _start_scan(self):
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showwarning("No folder", "Please enter or select a folder path.")
            return
        if not os.path.isdir(folder):
            messagebox.showerror("Invalid folder",
                                 f"'{folder}' does not exist or is not a directory.")
            return

        self._clear_results()
        self._cancel_flag = False
        self.scan_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        mode = self.mode_var.get()

        def progress_callback(current, total, filename):
            if total == 0:
                return
            if hasattr(self, '_cancel_flag') and self._cancel_flag:
                return
            self.root.after(0, self._update_progress, current, total, filename)

        def scan_thread():
            try:
                if mode == "tags":
                    self.model.scan_tags(folder, progress_callback)
                elif mode == "acoustic":
                    self.model.scan_acoustic(folder, progress_callback)
                else:
                    self.model.scan_hybrid(folder, progress_callback)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.root.after(0, messagebox.showerror, "Error", f"Scan failed:\n{e}")
                self.root.after(0, self._scan_done)
                return
            if not self._cancel_flag:
                self.root.after(0, self._scan_done)
            else:
                self.root.after(0, self._scan_cancelled)

        threading.Thread(target=scan_thread, daemon=True).start()

    def _update_progress(self, current, total, filename):
        self.progress['value'] = (current / total) * 100 if total else 0
        mode_name = {"tags": "Tags", "acoustic": "Acoustic", "hybrid": "Hybrid"}
        mn = mode_name.get(self.mode_var.get(), "")
        self.status_var.set(f"[{mn}] {current}/{total} — {filename}")

    def _scan_done(self):
        self.scan_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        self.progress['value'] = 0
        total_dups = sum(len(g.duplicates) for g in self.model.groups)
        total_size = sum(d.path.stat().st_size for g in self.model.groups for d in g.duplicates)
        self.status_var.set(
            f"Done. Found {len(self.model.groups)} groups, {total_dups} duplicates.")
        self.summary_var.set(
            f"Groups: {len(self.model.groups)} | Duplicates: {total_dups} | "
            f"~{total_size / (1024*1024):.1f} MB")

        if not self.model.groups:
            ttk.Label(self.results_frame, text="No duplicates found.",
                      foreground="gray").pack(anchor=tk.W, pady=10)
        else:
            self.execute_btn.configure(state=tk.NORMAL)
            self.select_all_cb.configure(state=tk.NORMAL)
            self._render_groups()

    def _scan_cancelled(self):
        self.scan_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        self.progress['value'] = 0
        self.status_var.set("Scan cancelled.")
        self._clear_results()

    def _render_groups(self):
        for child in self.results_frame.winfo_children():
            child.destroy()
        self._group_widgets = []

        checked_default = self.select_all_var.get()

        for group in self.model.groups:
            mode_tag = {"tags": "[TAGS]", "acoustic": "[♪]", "hybrid": "[TAGS+♪]"}.get(
                group.match_type, "")

            header = f"{mode_tag}  {group.artist}  —  {group.title}"
            lf = ttk.LabelFrame(self.results_frame, text=header, padding=8)
            lf.pack(fill=tk.X, pady=(0, 8), padx=2)

            best_frame = ttk.Frame(lf)
            best_frame.pack(fill=tk.X)
            ttk.Label(best_frame, text="★ BEST", foreground="green",
                      font=("", 9, "bold")).pack(side=tk.LEFT)
            ttk.Label(best_frame, text=f"{group.best.path.name}").pack(side=tk.LEFT, padx=5)
            ttk.Label(best_frame, text=f"({group.best.bitrate} kbps)",
                      foreground="gray").pack(side=tk.LEFT)
            ttk.Label(best_frame, text=str(group.best.path.parent),
                      foreground="gray").pack(side=tk.LEFT, padx=10)

            ttk.Separator(lf, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

            for dup in group.duplicates:
                dup_frame = ttk.Frame(lf)
                dup_frame.pack(fill=tk.X, pady=1)

                var = tk.BooleanVar(value=checked_default)
                dup._checkbox_var = var
                cb = ttk.Checkbutton(dup_frame, variable=var)
                cb.pack(side=tk.LEFT)
                ttk.Label(dup_frame, text=f"{dup.path.name}").pack(side=tk.LEFT, padx=5)
                ttk.Label(dup_frame, text=f"({dup.bitrate} kbps)",
                          foreground="gray").pack(side=tk.LEFT)
                size_mb = dup.path.stat().st_size / (1024 * 1024)
                ttk.Label(dup_frame, text=f"  {size_mb:.1f} MB",
                          foreground="gray").pack(side=tk.LEFT)
                if dup.similarity > 0:
                    ttk.Label(dup_frame, text=f"  match: {dup.similarity:.0%}",
                              foreground="#b8860b").pack(side=tk.LEFT)
                ttk.Label(dup_frame, text=str(dup.path.parent),
                          foreground="gray").pack(side=tk.LEFT, padx=10)

    def _toggle_all(self):
        checked = self.select_all_var.get()
        for group in self.model.groups:
            for dup in group.duplicates:
                dup.selected = checked
                if hasattr(dup, '_checkbox_var'):
                    dup._checkbox_var.set(checked)

    def _execute(self):
        action = self.action_var.get()
        to_process = []
        for group in self.model.groups:
            for dup in group.duplicates:
                if hasattr(dup, '_checkbox_var') and dup._checkbox_var.get():
                    to_process.append(dup.path)

        if not to_process:
            messagebox.showinfo("Nothing selected",
                                "No duplicates are selected for action.")
            return

        action_text = "delete" if action == "delete" else "move to _duplicates"
        if not messagebox.askyesno(
            "Confirm",
            f"Are you sure you want to {action_text} {len(to_process)} file(s)?"
        ):
            return

        errors = 0
        for path in to_process:
            try:
                if action == "delete":
                    os.remove(path)
                else:
                    trash_dir = path.parent / "_duplicates"
                    trash_dir.mkdir(exist_ok=True)
                    dest = trash_dir / path.name
                    counter = 1
                    while dest.exists():
                        dest = trash_dir / f"{path.stem}_{counter}{path.suffix}"
                        counter += 1
                    shutil.move(str(path), str(dest))
            except OSError as e:
                errors += 1
                print(f"Error with {path}: {e}")

        if errors:
            messagebox.showwarning(
                "Done with errors",
                f"Processed {len(to_process) - errors} files, {errors} error(s)."
            )
        else:
            messagebox.showinfo("Done",
                                f"Successfully {action_text}d {len(to_process)} file(s).")

        self._clear_results()
        self._start_scan()

    def _clear_results(self):
        self.execute_btn.configure(state=tk.DISABLED)
        self.select_all_var.set(True)
        self.select_all_cb.configure(state=tk.DISABLED)
        self.summary_var.set("")
        self.model = DedupModel()
        for child in self.results_frame.winfo_children():
            child.destroy()
        self._group_widgets = []


# ──────────────────────── main ─────────────────────────────

if __name__ == '__main__':
    root = tk.Tk()
    app = DedupApp(root)
    root.mainloop()
