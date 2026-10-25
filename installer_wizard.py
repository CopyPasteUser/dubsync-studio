#!/usr/bin/env python3
"""Windows-style graphical wizard for installing DubSync Studio on Linux.

Uses Tkinter from the Python standard library so the installer can start before
PySide6 and the application's other dependencies have been installed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "DubSync Studio"
APP_VERSION = "1.5.14"
APP_ID = "dubsync-studio"


def config_root(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    base = Path(values.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    return base / APP_ID


def default_install_dir(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    meta = config_root(values) / "install.json"
    try:
        record = json.loads(meta.read_text(encoding="utf-8"))
        current = Path(str(record.get("install_dir", ""))).expanduser()
        marker = current / ".dubsync-install.json"
        if current.is_absolute() and marker.is_file() and (current / "dubsync" / "__init__.py").is_file():
            return current
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    data_home = Path(values.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))).expanduser()
    return data_home / APP_ID / "app"


def normalize_install_dir(raw: str, home: Path | None = None) -> Path:
    """Normalize a destination and reject broad/system-managed locations."""
    if not raw.strip():
        raise ValueError("Bitte wähle einen Installationsordner aus.")
    target = Path(raw.strip()).expanduser().resolve(strict=False)
    home_path = (home or Path.home()).expanduser().resolve(strict=False)
    if target == Path("/") or target == home_path:
        raise ValueError("Bitte einen eigenen Unterordner wählen, nicht / oder das Home-Verzeichnis.")
    # An explicit home argument is used by isolated validation/tests; in that
    # case don't accidentally inherit this process's unrelated XDG directories.
    env = {} if home is not None else os.environ
    config_base = Path(env.get("XDG_CONFIG_HOME", str(home_path / ".config"))).expanduser().resolve(strict=False)
    cache_base = Path(env.get("XDG_CACHE_HOME", str(home_path / ".cache"))).expanduser().resolve(strict=False)
    state_base = Path(env.get("XDG_STATE_HOME", str(home_path / ".local" / "state"))).expanduser().resolve(strict=False)
    data_base = Path(env.get("XDG_DATA_HOME", str(home_path / ".local" / "share"))).expanduser().resolve(strict=False)
    protected_roots = {
        config_base, cache_base, state_base,
        data_base / "applications", data_base / "icons",
    }
    protected_exact = {
        home_path / ".local", home_path / ".local" / "bin",
        data_base, data_base / "applications", data_base / "icons",
    }
    if target in protected_exact or any(target == base or base in target.parents for base in protected_roots):
        raise ValueError("Dieser Pfad ist für Einstellungen, Cache oder Desktop-Dateien reserviert.")
    if target.exists() and not target.is_dir():
        raise ValueError("Der gewählte Installationspfad ist bereits eine Datei.")
    return target


def system_report() -> dict[str, Any]:
    tkinter_ok = False
    tkinter_version = "nicht verfügbar"
    try:
        import tkinter  # noqa: F401
        import tkinter.ttk  # noqa: F401
        tkinter_ok = True
        tkinter_version = str(tkinter.TkVersion)
    except Exception:
        pass
    source = Path(__file__).resolve().parent
    tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    return {
        "application": APP_NAME,
        "application_version": APP_VERSION,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_supported": sys.version_info >= (3, 10),
        "tkinter": tkinter_ok,
        "tk_version": tkinter_version,
        "ffmpeg": tools["ffmpeg"],
        "ffprobe": tools["ffprobe"],
        "install_script": (source / "install.sh").is_file(),
        "setup_script": (source / "setup.sh").is_file(),
    }


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grafischer DubSync-Studio-Installationsassistent")
    parser.add_argument("--check", action="store_true", help="Voraussetzungen prüfen, ohne GUI zu öffnen")
    parser.add_argument("--json", action="store_true", help="Systemcheck als JSON ausgeben (mit --check)")
    parser.add_argument("--version", action="version", version=f"DubSync Studio Installer {APP_VERSION}")
    args = parser.parse_args(argv)
    if args.json and not args.check:
        parser.error("--json kann nur zusammen mit --check verwendet werden.")
    if args.check:
        report = system_report()
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in report.items()))
        return 0 if report["python_supported"] and report["tkinter"] and report["install_script"] and report["setup_script"] else 1
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        print("Der grafische Installer benötigt Tkinter. Installiere das Tk-Paket deiner Distribution oder nutze ./install.sh.", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        return 2
    try:
        root = tk.Tk()
    except Exception as exc:
        print("Kein grafischer Desktop verfügbar. Starte in einer grafischen Sitzung oder nutze ./install.sh.", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        return 2
    InstallerWizard(root, tk, ttk, filedialog, messagebox).run()
    return 0


class InstallerWizard:
    PAGE_NAMES = ("Willkommen", "Zielordner", "Optionen", "Zusammenfassung", "Installation", "Fertig")

    def __init__(self, root, tk, ttk, filedialog, messagebox):
        self.root, self.tk, self.ttk = root, tk, ttk
        self.filedialog, self.messagebox = filedialog, messagebox
        self.source_dir = Path(__file__).resolve().parent
        self.install_path = tk.StringVar(value=str(default_install_dir()))
        self.desktop_shortcut = tk.BooleanVar(value=False)
        self.launch_after = tk.BooleanVar(value=True)
        self.current_page = 0
        self.install_state = "idle"
        self.install_returncode: int | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.pages: list[Any] = []
        self.step_labels: list[tuple[Any, Any, Any]] = []
        self._build_shell()
        self._build_pages()
        self._show_page(0)

    def run(self):
        self.root.mainloop()

    def _build_shell(self):
        tk, ttk = self.tk, self.ttk
        self.root.title(f"{APP_NAME} – Installationsassistent")
        self.root.geometry("850x590")
        self.root.minsize(760, 540)
        self.root.configure(bg="#f3f6fb")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<Escape>", lambda _event: self._escape())
        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Wizard.TFrame", background="#f3f6fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Wizard.TLabel", background="#f3f6fb", foreground="#172033", font=("TkDefaultFont", 10))
        style.configure("Title.TLabel", background="#ffffff", foreground="#14243b", font=("TkDefaultFont", 21, "bold"))
        style.configure("Subtitle.TLabel", background="#ffffff", foreground="#5d6c83", font=("TkDefaultFont", 10))
        style.configure("Section.TLabel", background="#ffffff", foreground="#172033", font=("TkDefaultFont", 12, "bold"))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#66758a", font=("TkDefaultFont", 9))
        style.configure("Accent.TButton", font=("TkDefaultFont", 10, "bold"), padding=(16, 8))
        style.configure("TButton", padding=(12, 7))
        style.configure("TEntry", padding=7)
        style.configure("TProgressbar", troughcolor="#e6ebf3", background="#1877d2", bordercolor="#e6ebf3", lightcolor="#1877d2", darkcolor="#1877d2")

        header = tk.Frame(self.root, bg="#101d33", height=100)
        header.pack(fill="x")
        header.pack_propagate(False)
        logo = tk.Canvas(header, width=62, height=62, bg="#101d33", highlightthickness=0)
        logo.pack(side="left", padx=(24, 14), pady=18)
        self._draw_logo(logo)
        titlebox = tk.Frame(header, bg="#101d33")
        titlebox.pack(side="left", fill="both", expand=True, pady=20)
        tk.Label(titlebox, text=APP_NAME, bg="#101d33", fg="#ffffff", font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        tk.Label(titlebox, text="Installationsassistent", bg="#101d33", fg="#aebdd3", font=("TkDefaultFont", 10)).pack(anchor="w", pady=(3, 0))
        tk.Label(header, text=f"VERSION {APP_VERSION}", bg="#101d33", fg="#aebdd3", font=("TkDefaultFont", 9, "bold")).pack(side="right", padx=24)

        body = ttk.Frame(self.root, style="Wizard.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.sidebar = tk.Frame(body, bg="#e9eef6", width=194)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        tk.Label(self.sidebar, text="INSTALLATION", bg="#e9eef6", fg="#64728a", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", padx=20, pady=(26, 14))
        for title in self.PAGE_NAMES:
            row = tk.Frame(self.sidebar, bg="#e9eef6")
            row.pack(fill="x", padx=12, pady=4)
            bullet = tk.Label(row, text="○", width=2, bg="#e9eef6", fg="#71819a", font=("TkDefaultFont", 11, "bold"))
            bullet.pack(side="left", padx=(4, 8))
            label = tk.Label(row, text=title, bg="#e9eef6", fg="#52617a", anchor="w", font=("TkDefaultFont", 10))
            label.pack(side="left", fill="x", expand=True, pady=7)
            self.step_labels.append((bullet, label, row))

        self.content = ttk.Frame(body, style="Wizard.TFrame", padding=(24, 22, 24, 18))
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        footer = tk.Frame(self.root, bg="#ffffff", highlightbackground="#dbe2ec", highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        tk.Label(footer, text="Installiert nur für dein Benutzerkonto · kein Root-Zugriff", bg="#ffffff", fg="#68758b", font=("TkDefaultFont", 9)).pack(side="left", padx=22)
        buttons = tk.Frame(footer, bg="#ffffff")
        buttons.pack(side="right", padx=18, pady=12)
        self.cancel_button = ttk.Button(buttons, text="Abbrechen", command=self._close)
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.back_button = ttk.Button(buttons, text="Zurück", command=self._back)
        self.back_button.pack(side="left", padx=(0, 8))
        self.next_button = ttk.Button(buttons, text="Weiter", style="Accent.TButton", command=self._next)
        self.next_button.pack(side="left")

    @staticmethod
    def _draw_logo(canvas):
        canvas.create_rectangle(3, 3, 59, 59, fill="#172d4c", outline="#314d72", width=1)
        for x, top, color in (
            (12, 29, "#47d9e8"), (19, 19, "#47d9e8"), (26, 25, "#47d9e8"),
            (33, 13, "#a78bfa"), (40, 22, "#a78bfa"), (47, 17, "#7ce5c3"),
        ):
            canvas.create_line(x, 34, x, top, fill=color, width=3, capstyle="round")
        canvas.create_line(10, 39, 49, 39, fill="#ffffff", width=2)
        canvas.create_oval(27, 35, 35, 43, fill="#7ce5c3", outline="#7ce5c3")

    def _new_page(self):
        frame = self.ttk.Frame(self.content, style="Wizard.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        self.pages.append(frame)
        return frame

    def _card(self, parent):
        frame = self.ttk.Frame(parent, style="Card.TFrame", padding=(24, 22))
        frame.pack(fill="both", expand=True)
        return frame

    def _heading(self, parent, title: str, subtitle: str):
        self.ttk.Label(parent, text=title, style="Title.TLabel", wraplength=510).pack(anchor="w")
        self.ttk.Label(parent, text=subtitle, style="Subtitle.TLabel", wraplength=510, justify="left").pack(anchor="w", pady=(8, 18))

    def _build_pages(self):
        tk, ttk = self.tk, self.ttk
        # 0: Welcome
        page = self._card(self._new_page())
        self._heading(page, "Willkommen bei DubSync Studio", "Dieser Assistent installiert die App für dein Benutzerkonto und richtet einen Starter im Anwendungsmenü ein.")
        for title, desc in (
            ("Mehrsprachige Serien", "Episoden analysieren und mehrere Audiosprachen in MKV-Dateien zusammenführen."),
            ("Lokale Installation", "Eigene Python-Umgebung, kein Root-Zugriff und keine Änderung deiner Desktop-Einstellungen."),
            ("Sicher aktualisierbar", "Projekte, Einstellungen, Cache und Jobverlauf bleiben getrennt von den Programmdateien."),
        ):
            row = tk.Frame(page, bg="#ffffff")
            row.pack(fill="x", pady=7)
            tk.Label(row, text="✓", bg="#e4f7f2", fg="#147d65", width=3, font=("TkDefaultFont", 11, "bold")).pack(side="left", padx=(0, 12), ipady=7)
            column = tk.Frame(row, bg="#ffffff")
            column.pack(side="left", fill="x", expand=True)
            ttk.Label(column, text=title, style="Section.TLabel").pack(anchor="w")
            ttk.Label(column, text=desc, style="Muted.TLabel", wraplength=440, justify="left").pack(anchor="w", pady=(3, 0))
        self.ttk.Label(page, text="Dauer: abhängig von Downloadgeschwindigkeit und verfügbaren Python-Paketen.", style="Muted.TLabel", wraplength=500).pack(anchor="w", side="bottom", pady=(15, 0))

        # 1: Destination
        page = self._card(self._new_page())
        self._heading(page, "Wohin soll DubSync Studio installiert werden?", "Wähle einen eigenen Programmordner. Das Ziel darf leer sein oder eine bereits von DubSync verwaltete Installation enthalten.")
        ttk.Label(page, text="Installationsordner", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        path_row = ttk.Frame(page, style="Card.TFrame")
        path_row.pack(fill="x")
        self.path_entry = ttk.Entry(path_row, textvariable=self.install_path)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(path_row, text="Durchsuchen…", command=self._browse)
        self.browse_button.pack(side="left", padx=(8, 0))
        ttk.Label(page, text="Standard: ~/.local/share/dubsync-studio/app", style="Muted.TLabel").pack(anchor="w", pady=(8, 18))
        ttk.Label(page, text="Hinweis", style="Section.TLabel").pack(anchor="w")
        ttk.Label(page, text="Bei einem Update wird eine vorhandene DubSync-Installation am gewählten Ziel aktualisiert. Nicht zugehörige Dateien in einem belegten Ordner werden nicht überschrieben.", style="Muted.TLabel", wraplength=490, justify="left").pack(anchor="w", pady=(5, 0))

        # 2: Options
        page = self._card(self._new_page())
        self._heading(page, "Installationsoptionen", "Der Menüeintrag wird immer erstellt. Hier kannst du weitere Komfortoptionen auswählen.")
        ttk.Checkbutton(page, text="Zusätzliche Verknüpfung auf dem Desktop erstellen", variable=self.desktop_shortcut).pack(anchor="w", pady=(4, 12))
        ttk.Checkbutton(page, text="DubSync Studio nach der Installation starten", variable=self.launch_after).pack(anchor="w", pady=(4, 12))
        ttk.Separator(page).pack(fill="x", pady=14)
        ttk.Label(page, text="Datenschutz und Kontrolle", style="Section.TLabel").pack(anchor="w")
        ttk.Label(page, text="Der Assistent installiert nur für dein Benutzerkonto. Systempakete werden nicht automatisch mit Root-Rechten installiert. Einstellungen und Medienprojekte bleiben bei einer normalen Deinstallation erhalten.", style="Muted.TLabel", wraplength=490, justify="left").pack(anchor="w", pady=(6, 0))

        # 3: Review
        page = self._card(self._new_page())
        self._heading(page, "Bereit zur Installation", "Prüfe den Zielordner und die Voraussetzungen. Mit „Installieren“ startet die Einrichtung.")
        self.summary = tk.Text(page, height=6, wrap="word", relief="flat", bg="#f3f6fb", fg="#24334b", padx=12, pady=10, font=("TkFixedFont", 9), state="disabled")
        self.summary.pack(fill="x", pady=(0, 14))
        ttk.Label(page, text="Systemprüfung", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.checks_text = tk.Text(page, height=6, wrap="word", relief="flat", bg="#f3f6fb", fg="#24334b", padx=12, pady=10, font=("TkFixedFont", 9), state="disabled")
        self.checks_text.pack(fill="both", expand=True)

        # 4: Progress
        page = self._card(self._new_page())
        self._heading(page, "DubSync Studio wird eingerichtet", "Bitte lass den Assistenten geöffnet, während die Python-Umgebung erstellt und die Abhängigkeiten installiert werden. Das Fortschrittsprotokoll unten zeigt die einzelnen Installationsschritte.")
        self.status_label = ttk.Label(page, text="Warte auf den Installationsprozess …", style="Section.TLabel")
        self.status_label.pack(anchor="w", pady=(0, 8))
        self.progress_bar = ttk.Progressbar(page, mode="indeterminate", length=400)
        self.progress_bar.pack(fill="x", pady=(0, 12))
        log_frame = ttk.Frame(page, style="Card.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=10, wrap="word", bg="#101d33", fg="#d8e4f4", insertbackground="#ffffff", relief="flat", padx=12, pady=10, font=("TkFixedFont", 9), state="disabled")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.failure_label = ttk.Label(page, text="", foreground="#a12331", background="#ffffff", wraplength=490, justify="left")
        self.failure_label.pack(anchor="w", pady=(8, 0))

        # 5: Finish
        page = self._card(self._new_page())
        self.finish_icon = tk.Label(page, text="✓", bg="#e4f7f2", fg="#147d65", font=("TkDefaultFont", 30, "bold"), width=3)
        self.finish_icon.pack(anchor="w", pady=(4, 14))
        self.finish_title = ttk.Label(page, text="Installation abgeschlossen", style="Title.TLabel")
        self.finish_title.pack(anchor="w")
        self.finish_description = ttk.Label(page, text="DubSync Studio ist installiert und im Anwendungsmenü verfügbar.", style="Subtitle.TLabel", wraplength=500, justify="left")
        self.finish_description.pack(anchor="w", pady=(8, 18))
        ttk.Label(page, text="Installationsordner", style="Section.TLabel").pack(anchor="w")
        self.finish_path = ttk.Label(page, textvariable=self.install_path, style="Muted.TLabel", wraplength=500, justify="left")
        self.finish_path.pack(anchor="w", pady=(5, 18))
        self.launch_button = ttk.Button(page, text="DubSync Studio starten", style="Accent.TButton", command=self._launch_app)
        self.launch_button.pack(anchor="w")

    def _browse(self):
        current = self.install_path.get().strip() or str(default_install_dir())
        initial = str(Path(current).expanduser().parent)
        picked = self.filedialog.askdirectory(title="Installationsordner auswählen", initialdir=initial, mustexist=False)
        if picked:
            proposed = Path(picked).expanduser()
            # The picker selects the destination folder itself. It doesn't silently add a second app folder.
            self.install_path.set(str(proposed.resolve(strict=False)))

    def _show_page(self, index: int):
        self.current_page = index
        for page in self.pages:
            page.grid_remove()
        self.pages[index].grid()
        for step, (bullet, label, row) in enumerate(self.step_labels):
            if step < index:
                bullet.configure(text="✓", fg="#147d65")
                label.configure(fg="#53647a", font=("TkDefaultFont", 10))
            elif step == index:
                bullet.configure(text="●", fg="#1877d2")
                label.configure(fg="#14243b", font=("TkDefaultFont", 10, "bold"))
            else:
                bullet.configure(text="○", fg="#71819a")
                label.configure(fg="#52617a", font=("TkDefaultFont", 10))
        self._update_buttons()
        if index == 3:
            self._update_summary()

    def _update_buttons(self):
        installing = self.install_state == "running"
        self.back_button.configure(state="normal" if self.current_page > 0 and not installing and self.current_page not in (5,) else "disabled")
        self.cancel_button.configure(state="disabled" if installing else "normal", text="Schließen" if self.current_page == 5 else "Abbrechen")
        if self.current_page == 3:
            self.next_button.configure(text="Installieren", state="normal")
        elif self.current_page == 4 and self.install_state == "failed":
            self.next_button.configure(text="Erneut versuchen", state="normal")
        elif self.current_page == 4:
            self.next_button.configure(text="Installiert …", state="disabled")
        elif self.current_page == 5:
            self.next_button.configure(text="Fertigstellen", state="normal")
        else:
            self.next_button.configure(text="Weiter", state="normal")

    def _update_summary(self):
        destination = self.install_path.get().strip()
        report = system_report()
        lines = [
            f"Ziel: {destination}",
            f"Desktop-Verknüpfung: {'Ja' if self.desktop_shortcut.get() else 'Nein'}",
            f"Nach Installation starten: {'Ja' if self.launch_after.get() else 'Nein'}",
            "Anwendungsmenü: Ja",
        ]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

        checks = [
            f"Python {report['python']}: {'OK' if report['python_supported'] else 'FEHLT (benötigt 3.10+)'}",
            f"Tkinter {report['tk_version']}: {'OK' if report['tkinter'] else 'FEHLT'}",
            f"FFmpeg: {report['ffmpeg'] or 'nicht gefunden'}",
            f"FFprobe: {report['ffprobe'] or 'nicht gefunden'}",
        ]
        if not report["ffmpeg"] or not report["ffprobe"]:
            checks.append("Hinweis: Ohne FFmpeg/FFprobe wird setup.sh abbrechen. Installiere die Medienwerkzeuge über deinen Paketmanager.")
        if not report["python_supported"]:
            checks.append("FEHLER: Python 3.10 oder neuer ist erforderlich.")
        self.checks_text.configure(state="normal")
        self.checks_text.delete("1.0", "end")
        self.checks_text.insert("1.0", "\n".join(checks))
        self.checks_text.configure(state="disabled")

    def _next(self):
        if self.current_page == 0:
            self._show_page(1)
        elif self.current_page == 1:
            try:
                normalize_install_dir(self.install_path.get())
            except ValueError as exc:
                self.messagebox.showerror("Installationspfad prüfen", str(exc), parent=self.root)
                return
            self._show_page(2)
        elif self.current_page == 2:
            self._show_page(3)
        elif self.current_page == 3:
            self._start_install()
        elif self.current_page == 4 and self.install_state == "failed":
            self._start_install()
        elif self.current_page == 5:
            if self.launch_after.get() and self.install_state == "installed":
                self._launch_app()
                return
            self.root.destroy()

    def _back(self):
        if self.install_state == "running":
            return
        if self.current_page == 4 and self.install_state == "failed":
            self._show_page(3)
        elif self.current_page > 0:
            self._show_page(self.current_page - 1)

    def _start_install(self):
        try:
            target = normalize_install_dir(self.install_path.get())
        except ValueError as exc:
            self.messagebox.showerror("Installationspfad prüfen", str(exc), parent=self.root)
            self._show_page(1)
            return
        script = self.source_dir / "install.sh"
        if not script.is_file():
            self.messagebox.showerror("Installationsdatei fehlt", f"Nicht gefunden:\n{script}", parent=self.root)
            return
        report = system_report()
        if not report["python_supported"]:
            self.messagebox.showerror("Python zu alt", "DubSync Studio benötigt Python 3.10 oder neuer.", parent=self.root)
            return
        command = ["bash", str(script), "--install-dir", str(target)]
        if self.desktop_shortcut.get():
            command.append("--desktop-shortcut")
        env = os.environ.copy()
        env["PYTHON"] = sys.executable
        env["PYTHONUNBUFFERED"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        self.install_state = "running"
        self.install_returncode = None
        self.failure_label.configure(text="")
        self.status_label.configure(text="Installation wird vorbereitet …")
        self.progress_bar.start(10)
        self._append_log("Befehl: " + " ".join(command))
        self._show_page(4)
        self._update_buttons()
        self.worker = threading.Thread(target=self._install_worker, args=(command, env), daemon=True)
        self.worker.start()
        self.root.after(100, self._drain_events)

    def _install_worker(self, command: list[str], env: dict[str, str]):
        try:
            process = subprocess.Popen(
                command, cwd=self.source_dir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            self.process = process
            if process.stdout is not None:
                for line in process.stdout:
                    self.events.put(("line", line.rstrip("\r\n")))
            returncode = process.wait()
            self.events.put(("done", returncode))
        except Exception as exc:
            self.events.put(("line", f"Installer konnte nicht gestartet werden: {exc}"))
            self.events.put(("done", 127))

    def _drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "line":
                    line = str(value)
                    self._append_log(line)
                    lower = line.lower()
                    if "setup abgeschlossen" in lower:
                        self.status_label.configure(text="Python-Umgebung eingerichtet …")
                    elif "desktop-menüeintrag" in lower:
                        self.status_label.configure(text="Desktop-Integration wird eingerichtet …")
                    elif "anwendung kopiert" in lower:
                        self.status_label.configure(text="Anwendungsdateien werden kopiert …")
                elif kind == "done":
                    self.install_returncode = int(value)
                    self.progress_bar.stop()
                    self.process = None
                    if self.install_returncode == 0:
                        self.install_state = "installed"
                        self.status_label.configure(text="Installation erfolgreich abgeschlossen.")
                        self.finish_title.configure(text="Installation abgeschlossen")
                        self.finish_description.configure(text="DubSync Studio ist installiert. Du findest es im Anwendungsmenü unter DubSync Studio.")
                        self.launch_button.configure(state="normal")
                        if not self.launch_after.get():
                            self.launch_button.configure(text="DubSync Studio später starten")
                            self.launch_button.configure(state="normal")
                        self._show_page(5)
                        self._append_log("\nInstallation erfolgreich.")
                    else:
                        self.install_state = "failed"
                        self.status_label.configure(text="Installation fehlgeschlagen.")
                        report = system_report()
                        hint = "Prüfe die Meldungen. Du kannst zurückgehen, Voraussetzungen korrigieren und erneut versuchen."
                        missing = [name for name in ("ffmpeg", "ffprobe") if not report[name]]
                        if missing:
                            hint += " Fehlend: " + ", ".join(missing) + ". Systempakete bitte manuell über den Paketmanager installieren."
                        self.failure_label.configure(text=hint)
                        self._append_log(f"\nINSTALLER FEHLGESCHLAGEN (Code {self.install_returncode}).")
                        self._update_buttons()
                self.events.task_done()
        except queue.Empty:
            pass
        if self.install_state == "running":
            self.root.after(100, self._drain_events)

    def _append_log(self, text: str):
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _launch_app(self):
        launcher = Path.home() / ".local" / "bin" / APP_ID
        if not launcher.is_file():
            self.messagebox.showerror("Starter nicht gefunden", f"Installation ist abgeschlossen, aber der Starter fehlt:\n{launcher}", parent=self.root)
            return
        try:
            subprocess.Popen([str(launcher)], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.messagebox.showerror("Start fehlgeschlagen", str(exc), parent=self.root)
            return
        self.root.destroy()

    def _escape(self):
        if self.current_page not in (4, 5) and self.install_state != "running":
            self._close()

    def _close(self):
        if self.install_state == "running":
            self.messagebox.showinfo("Installation läuft", "Bitte warte, bis die Installation abgeschlossen oder fehlgeschlagen ist.", parent=self.root)
            return
        self.root.destroy()


def main() -> int:
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
