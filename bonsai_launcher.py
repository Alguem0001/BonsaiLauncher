#!/usr/bin/env python3
"""
Bonsai Launcher â€” starts llama-server (Vulkan) + opens the WebUI,
with model selection and built-in agent tools enabled.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Paths (works from source or from a frozen PyInstaller .exe)
# ---------------------------------------------------------------------------

def app_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE = app_base()
DEFAULT_LLAMA_BIN = Path(r"C:\Users\geron\OneDrive\Desktop\AI\Bansai Llama.cpp\llama.cpp\build\bin")
DEFAULT_MODELS = Path(r"C:\Users\geron\OneDrive\Desktop\AI\Bansai Llama.cpp\models")
CONFIG_PATH = BASE / "launcher_config.json"

# Known models (repo_id, filename, display label, optional mmproj filename)
KNOWN_MODELS = [
    {
        "id": "bonsai-27b-q1",
        "label": "Bonsai 27B Â· 1-bit (Q1_0)  [recomendado]",
        "repo": "prism-ml/Bonsai-27B-gguf",
        "file": "Bonsai-27B-Q1_0.gguf",
        "mmproj": "Bonsai-27B-mmproj-Q8_0.gguf",
    },
    {
        "id": "ternary-27b-q2",
        "label": "Ternary-Bonsai 27B Â· Q2_0 (group-128 demo)",
        "repo": "prism-ml/Ternary-Bonsai-27B-gguf",
        "file": "Ternary-Bonsai-27B-Q2_0.gguf",
        "mmproj": None,
    },
    {
        "id": "ternary-27b-q2-g64",
        "label": "Ternary-Bonsai 27B Â· Q2_0_g64 (mainline)",
        "repo": "prism-ml/Ternary-Bonsai-27B-gguf",
        "file": "Ternary-Bonsai-27B-Q2_0_g64.gguf",
        "mmproj": None,
    },
    {
        "id": "ternary-8b-q2-g64",
        "label": "Ternary-Bonsai 8B Â· Q2_0_g64",
        "repo": "prism-ml/Ternary-Bonsai-8B-gguf",
        "file": "Ternary-Bonsai-8B-Q2_0_g64.gguf",
        "mmproj": None,
    },
    {
        "id": "ternary-1.7b-q2-g64",
        "label": "Ternary-Bonsai 1.7B Â· Q2_0_g64",
        "repo": "prism-ml/Ternary-Bonsai-1.7B-gguf",
        "file": "Ternary-Bonsai-1.7B-Q2_0_g64.gguf",
        "mmproj": None,
    },
    {
        "id": "bonsai-8b-q1",
        "label": "Bonsai 8B Â· 1-bit (Q1_0)",
        "repo": "prism-ml/Bonsai-8B-gguf",
        "file": "Bonsai-8B-Q1_0.gguf",
        "mmproj": None,
    },
]


def load_config() -> dict:
    defaults = {
        "llama_bin": str(DEFAULT_LLAMA_BIN),
        "models_dir": str(DEFAULT_MODELS),
        "host": "127.0.0.1",
        "port": 8080,
        "ctx": 8192,
        "ngl": 99,
        "tools": "all",
        "jinja": True,
        "last_model": "bonsai-27b-q1",
        "extra_args": "",
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception:
            pass
    return defaults


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def find_server_exe(llama_bin: Path) -> Path | None:
    for name in ("llama-server.exe", "llama-server"):
        p = llama_bin / name
        if p.exists():
            return p
    return None


def list_local_ggufs(models_dir: Path) -> list[Path]:
    if not models_dir.is_dir():
        return []
    files = []
    for p in models_dir.rglob("*.gguf"):
        # skip mmproj as primary models
        if "mmproj" in p.name.lower():
            continue
        files.append(p)
    return sorted(files, key=lambda x: x.name.lower())


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class LauncherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Bonsai Launcher â€” llama.cpp + WebUI")
        self.root.minsize(640, 520)
        self.cfg = load_config()
        self.proc: subprocess.Popen | None = None
        self.log_lock = threading.Lock()

        self._build_ui()
        self.refresh_models()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_status()

    # ---- UI ----
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        # Paths
        paths = ttk.LabelFrame(frm, text="Caminhos", padding=8)
        paths.pack(fill=tk.X, **pad)

        self.var_bin = tk.StringVar(value=self.cfg["llama_bin"])
        self.var_models = tk.StringVar(value=self.cfg["models_dir"])

        ttk.Label(paths, text="Pasta do llama-server:").grid(row=0, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.var_bin, width=60).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(paths, text="â€¦", width=3, command=self.browse_bin).grid(row=0, column=2)

        ttk.Label(paths, text="Pasta de modelos:").grid(row=1, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.var_models, width=60).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(paths, text="â€¦", width=3, command=self.browse_models).grid(row=1, column=2)
        paths.columnconfigure(1, weight=1)

        # Model
        mdl = ttk.LabelFrame(frm, text="Modelo", padding=8)
        mdl.pack(fill=tk.X, **pad)

        self.var_model_choice = tk.StringVar()
        self.cmb_model = ttk.Combobox(mdl, textvariable=self.var_model_choice, state="readonly", width=70)
        self.cmb_model.grid(row=0, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Button(mdl, text="Atualizar lista", command=self.refresh_models).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(mdl, text="Escolher .ggufâ€¦", command=self.browse_gguf).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Button(mdl, text="Baixar modelo selecionado (HF)", command=self.download_selected).grid(
            row=1, column=2, sticky="e", pady=4
        )
        mdl.columnconfigure(0, weight=1)

        known = ttk.LabelFrame(frm, text="CatÃ¡logo PrismML (baixar)", padding=8)
        known.pack(fill=tk.X, **pad)
        self.var_known = tk.StringVar()
        labels = [k["label"] for k in KNOWN_MODELS]
        self.cmb_known = ttk.Combobox(known, textvariable=self.var_known, values=labels, state="readonly", width=70)
        self.cmb_known.grid(row=0, column=0, sticky="ew")
        # default Bonsai 27B 1-bit
        for i, k in enumerate(KNOWN_MODELS):
            if k["id"] == self.cfg.get("last_model", "bonsai-27b-q1"):
                self.cmb_known.current(i)
                break
        else:
            self.cmb_known.current(0)
        ttk.Button(known, text="Baixar do HuggingFace", command=self.download_known).grid(row=0, column=1, padx=6)
        known.columnconfigure(0, weight=1)

        # Options
        opts = ttk.LabelFrame(frm, text="OpÃ§Ãµes do servidor", padding=8)
        opts.pack(fill=tk.X, **pad)

        self.var_host = tk.StringVar(value=self.cfg["host"])
        self.var_port = tk.IntVar(value=int(self.cfg["port"]))
        self.var_ctx = tk.IntVar(value=int(self.cfg["ctx"]))
        self.var_ngl = tk.IntVar(value=int(self.cfg["ngl"]))
        self.var_tools = tk.BooleanVar(value=bool(self.cfg.get("tools")))
        self.var_jinja = tk.BooleanVar(value=bool(self.cfg.get("jinja", True)))
        self.var_vision = tk.BooleanVar(value=True)
        self.var_extra = tk.StringVar(value=self.cfg.get("extra_args", ""))

        r = 0
        ttk.Label(opts, text="Host:").grid(row=r, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.var_host, width=16).grid(row=r, column=1, sticky="w")
        ttk.Label(opts, text="Porta:").grid(row=r, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(opts, textvariable=self.var_port, width=8).grid(row=r, column=3, sticky="w")
        r += 1
        ttk.Label(opts, text="Context (-c):").grid(row=r, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.var_ctx, width=10).grid(row=r, column=1, sticky="w")
        ttk.Label(opts, text="GPU layers (-ngl):").grid(row=r, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(opts, textvariable=self.var_ngl, width=8).grid(row=r, column=3, sticky="w")
        r += 1
        ttk.Checkbutton(
            opts,
            text="Ativar Built-in Tools (--tools all)  [necessÃ¡rio para Grok Tools / agent tools na WebUI]",
            variable=self.var_tools,
        ).grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        ttk.Checkbutton(opts, text="Jinja chat template (--jinja)", variable=self.var_jinja).grid(
            row=r, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(opts, text="Carregar mmproj (visÃ£o) se existir", variable=self.var_vision).grid(
            row=r, column=2, columnspan=2, sticky="w"
        )
        r += 1
        ttk.Label(opts, text="Args extras:").grid(row=r, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.var_extra, width=50).grid(row=r, column=1, columnspan=3, sticky="ew")
        opts.columnconfigure(1, weight=1)

        # Buttons
        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, **pad)
        self.btn_start = ttk.Button(btns, text="â–¶  Iniciar llama + WebUI", command=self.start_server)
        self.btn_start.pack(side=tk.LEFT, padx=4)
        self.btn_stop = ttk.Button(btns, text="â–   Parar", command=self.stop_server, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Abrir WebUI no browser", command=self.open_browser).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Salvar config", command=self.persist).pack(side=tk.RIGHT, padx=4)

        self.var_status = tk.StringVar(value="Parado")
        ttk.Label(frm, textvariable=self.var_status, font=("", 10, "bold")).pack(anchor="w", padx=10)

        logf = ttk.LabelFrame(frm, text="Log", padding=6)
        logf.pack(fill=tk.BOTH, expand=True, **pad)
        self.txt = tk.Text(logf, height=14, wrap=tk.WORD, font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ---- helpers ----
    def log(self, msg: str) -> None:
        def _():
            self.txt.insert(tk.END, msg + "\n")
            self.txt.see(tk.END)

        self.root.after(0, _)

    def browse_bin(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_bin.get())
        if d:
            self.var_bin.set(d)

    def browse_models(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_models.get())
        if d:
            self.var_models.set(d)
            self.refresh_models()

    def browse_gguf(self) -> None:
        f = filedialog.askopenfilename(
            initialdir=self.var_models.get(),
            filetypes=[("GGUF models", "*.gguf"), ("All", "*.*")],
        )
        if f:
            self.refresh_models()
            # select by full path if present
            vals = list(self.cmb_model["values"])
            if f not in vals:
                vals = [f] + vals
                self.cmb_model["values"] = vals
            self.var_model_choice.set(f)

    def refresh_models(self) -> None:
        models_dir = Path(self.var_models.get())
        files = list_local_ggufs(models_dir)
        display = []
        self._model_map: dict[str, Path] = {}
        for p in files:
            try:
                sz = human_size(p.stat().st_size)
            except OSError:
                sz = "?"
            label = f"{p.name}  ({sz})"
            display.append(label)
            self._model_map[label] = p
            self._model_map[str(p)] = p
        self.cmb_model["values"] = display
        if display:
            # prefer Bonsai-27B-Q1_0
            preferred = None
            for lab, path in self._model_map.items():
                if path.name == "Bonsai-27B-Q1_0.gguf":
                    preferred = lab
                    break
            self.var_model_choice.set(preferred or display[0])
        else:
            self.var_model_choice.set("")
            self.log(f"Nenhum .gguf em {models_dir}")

    def resolve_model_path(self) -> Path | None:
        choice = self.var_model_choice.get().strip()
        if not choice:
            return None
        if choice in self._model_map:
            return self._model_map[choice]
        p = Path(choice)
        return p if p.exists() else None

    def find_mmproj(self, model: Path) -> Path | None:
        models_dir = Path(self.var_models.get())
        # exact known name for 27B
        candidates = [
            models_dir / "Bonsai-27B-mmproj-Q8_0.gguf",
            models_dir / "Bonsai-27B-mmproj-BF16.gguf",
            model.parent / "Bonsai-27B-mmproj-Q8_0.gguf",
        ]
        # any mmproj near the model
        candidates.extend(sorted(model.parent.glob("*mmproj*.gguf")))
        candidates.extend(sorted(models_dir.glob("*mmproj*.gguf")))
        for c in candidates:
            if c.exists():
                return c
        return None

    def persist(self) -> None:
        self.cfg.update(
            {
                "llama_bin": self.var_bin.get(),
                "models_dir": self.var_models.get(),
                "host": self.var_host.get(),
                "port": int(self.var_port.get()),
                "ctx": int(self.var_ctx.get()),
                "ngl": int(self.var_ngl.get()),
                "tools": "all" if self.var_tools.get() else "",
                "jinja": self.var_jinja.get(),
                "extra_args": self.var_extra.get(),
                "last_model": self._known_id_from_label(self.var_known.get()) or self.cfg.get("last_model"),
            }
        )
        save_config(self.cfg)
        self.log("Config salva.")

    def _known_id_from_label(self, label: str) -> str | None:
        for k in KNOWN_MODELS:
            if k["label"] == label:
                return k["id"]
        return None

    def _known_from_label(self, label: str) -> dict | None:
        for k in KNOWN_MODELS:
            if k["label"] == label:
                return k
        return None

    # ---- download ----
    def download_known(self) -> None:
        k = self._known_from_label(self.var_known.get())
        if not k:
            messagebox.showwarning("Download", "Selecione um modelo do catÃ¡logo.")
            return
        threading.Thread(target=self._download_worker, args=(k,), daemon=True).start()

    def download_selected(self) -> None:
        # if current selection maps to a known catalog entry by filename, download that
        model = self.resolve_model_path()
        if model:
            for k in KNOWN_MODELS:
                if k["file"] == model.name:
                    threading.Thread(target=self._download_worker, args=(k,), daemon=True).start()
                    return
        # else download the catalog selection
        self.download_known()

    def _download_worker(self, k: dict) -> None:
        models_dir = Path(self.var_models.get())
        models_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"Baixando {k['file']} de {k['repo']} â€¦")
        try:
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(repo_id=k["repo"], filename=k["file"], local_dir=str(models_dir))
            self.log(f"OK modelo: {path}")
            if k.get("mmproj"):
                self.log(f"Baixando mmproj {k['mmproj']} â€¦")
                mp = hf_hub_download(repo_id=k["repo"], filename=k["mmproj"], local_dir=str(models_dir))
                self.log(f"OK mmproj: {mp}")
            self.root.after(0, self.refresh_models)
            self.root.after(0, lambda: messagebox.showinfo("Download", f"Download concluÃ­do:\n{k['file']}"))
        except Exception as e:
            self.log(f"ERRO download: {e}")
            self.root.after(0, lambda: messagebox.showerror("Download", str(e)))

    # ---- server ----
    def build_cmd(self) -> list[str]:
        llama_bin = Path(self.var_bin.get())
        server = find_server_exe(llama_bin)
        if not server:
            raise FileNotFoundError(f"llama-server.exe nÃ£o encontrado em {llama_bin}")

        model = self.resolve_model_path()
        if not model or not model.exists():
            raise FileNotFoundError("Selecione um modelo .gguf local (ou baixe pelo catÃ¡logo).")

        host = self.var_host.get().strip() or "127.0.0.1"
        port = int(self.var_port.get())
        ctx = int(self.var_ctx.get())
        ngl = int(self.var_ngl.get())

        cmd = [
            str(server),
            "-m",
            str(model),
            "--host",
            host,
            "--port",
            str(port),
            "-c",
            str(ctx),
            "-ngl",
            str(ngl),
        ]
        if self.var_jinja.get():
            cmd.append("--jinja")
        if self.var_tools.get():
            cmd.extend(["--tools", "all"])

        if self.var_vision.get():
            mm = self.find_mmproj(model)
            if mm:
                cmd.extend(["--mmproj", str(mm)])
                self.log(f"mmproj: {mm.name}")

        extra = self.var_extra.get().strip()
        if extra:
            # simple split on spaces (quoted paths not supported here â€” use short flags)
            cmd.extend(extra.split())

        return cmd

    def start_server(self) -> None:
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Server", "JÃ¡ estÃ¡ em execuÃ§Ã£o.")
            return
        try:
            self.persist()
            cmd = self.build_cmd()
        except Exception as e:
            messagebox.showerror("Erro", str(e))
            return

        llama_bin = Path(self.var_bin.get())
        env = os.environ.copy()
        env["PATH"] = str(llama_bin) + os.pathsep + env.get("PATH", "")

        self.log("Comando: " + " ".join(cmd))
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(llama_bin),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as e:
            messagebox.showerror("Erro ao iniciar", str(e))
            return

        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.var_status.set(f"Iniciandoâ€¦ PID {self.proc.pid}")
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._wait_and_open, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.log(line.rstrip())
        code = self.proc.poll()
        self.root.after(0, lambda: self._on_exit(code))

    def _on_exit(self, code: int | None) -> None:
        self.var_status.set(f"Parado (exit {code})")
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)

    def _wait_and_open(self) -> None:
        host = self.var_host.get().strip() or "127.0.0.1"
        port = int(self.var_port.get())
        url = f"http://{host}:{port}/"
        health = f"http://{host}:{port}/health"
        for i in range(180):  # up to ~3 min for large model load
            if self.proc is None or self.proc.poll() is not None:
                return
            try:
                with urlopen(health, timeout=2) as r:
                    if r.status == 200:
                        self.log(f"Server pronto: {url}")
                        self.root.after(0, lambda: self.var_status.set(f"Rodando em {url}"))
                        webbrowser.open(url)
                        return
            except (URLError, TimeoutError, OSError):
                time.sleep(1)
        self.log("Timeout esperando /health â€” abra a WebUI manualmente.")

    def stop_server(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.log("Parando serverâ€¦")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.var_status.set("Parado")
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)

    def open_browser(self) -> None:
        host = self.var_host.get().strip() or "127.0.0.1"
        port = int(self.var_port.get())
        webbrowser.open(f"http://{host}:{port}/")

    def _poll_status(self) -> None:
        if self.proc and self.proc.poll() is None:
            host = self.var_host.get().strip() or "127.0.0.1"
            port = int(self.var_port.get())
            try:
                with urlopen(f"http://{host}:{port}/health", timeout=1) as r:
                    if r.status == 200:
                        self.var_status.set(f"Rodando em http://{host}:{port}/  (PID {self.proc.pid})")
            except Exception:
                pass
        self.root.after(3000, self._poll_status)

    def on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            if messagebox.askyesno("Sair", "Parar o llama-server e fechar?"):
                self.stop_server()
            else:
                return
        self.persist()
        self.root.destroy()


def main() -> None:
    # HiDPI-ish on Windows
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

