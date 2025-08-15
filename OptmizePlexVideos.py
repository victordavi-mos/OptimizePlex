#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import ctypes
import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from math import floor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ===================== Alvos (ordem importa: 1080p antes de 720p) =====================
TARGETS = [
    ("Optimized-1080p", 1920, 1080, "8M", "16M", 19),
    ("Optimized-720p",  1280,  720, "4M",  "8M",  21),
]
VERSIONS_DIRNAME = "Plex Versions"
TARGET_CONTAINER = "mp4"

# Codecs/perfis Plex-friendly
VIDEO_CODEC_GPU = "h264_nvenc"
VIDEO_CODEC_CPU = "libx264"
VIDEO_PROFILE   = "high"
VIDEO_LEVEL     = "4.1"
AUDIO_CODEC     = "aac"
AUDIO_BITRATE   = "192k"
AUDIO_CHANNELS  = 2
TEXT_SUBS       = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
VIDEO_EXTS      = {".mkv", ".mp4", ".mov", ".avi", ".m4v", ".ts", ".m2ts", ".wmv", ".webm"}

# Defaults (ajustáveis por CLI)
DEFAULT_GPU_WORKERS = 2
DEFAULT_CPU_WORKERS = 0
DEFAULT_CPU_THREADS_CPUWORKER = 5
DEFAULT_FILTER_THREADS_GPU     = 1
DEFAULT_CPU_BUDGET_FOR_GPU     = 10

# ===================== Sinais/estado =====================
CANCELLED = False
def on_sigint(sig, frame):
    global CANCELLED
    CANCELLED = True
signal.signal(signal.SIGINT, on_sigint)

# ===================== Utilidades =====================
def which(bin_name: str) -> Optional[str]:
    from shutil import which as _which
    return _which(bin_name)

def run(cmd: List[str], check=True, capture=False) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)

def has_ffmpeg() -> bool:
    return which("ffmpeg") is not None and which("ffprobe") is not None

def has_nvenc() -> bool:
    try:
        out = run(["ffmpeg", "-hide_banner", "-encoders"], check=True, capture=True).stdout
        return "h264_nvenc" in out
    except Exception:
        return False

def has_filter(name: str) -> bool:
    try:
        out = run(["ffmpeg", "-hide_banner", "-filters"], check=True, capture=True).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == name:
                return True
    except Exception:
        pass
    return False

def ffprobe_meta(path: Path) -> Dict:
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
               check=True, capture=True)
    return json.loads(proc.stdout)

def ffprobe_size_fast(path: Path) -> Tuple[int, int]:
    try:
        proc = run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            check=True, capture=True
        )
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        if streams:
            w = int(streams[0].get("width") or 0)
            h = int(streams[0].get("height") or 0)
            return w, h
    except Exception:
        pass
    return 0, 0

def is_text_sub(codec_name: str) -> bool:
    return (codec_name or "").lower() in TEXT_SUBS

def iter_video_files(root: Path) -> List[Path]:
    files = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            if p.parent.name == VERSIONS_DIRNAME:
                continue
            if p.stem.endswith("(Optimized-1080p)") or p.stem.endswith("(Optimized-720p)"):
                continue
            files.append(p)
    return files

def make_output_path(src: Path, label: str, container: str) -> Path:
    out_dir = src.parent / VERSIONS_DIRNAME
    out_dir.mkdir(exist_ok=True)
    return out_dir / f"{src.stem} ({label}).{container}"

def sanitize_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", s)[:180]

# ===================== Console (3 blocos × 5 linhas) =====================
ANSI_CLEAR = "\x1b[2J\x1b[H"
SEP = "─" * 90

def enable_ansi_on_windows():
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            new_mode = mode.value | 0x0004
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass

class Dashboard:
    def __init__(self, worker_names: List[str], interval: float = 1.0):
        self.worker_names = worker_names[:3]
        self.interval = max(0.2, min(2.0, interval))
        self.state: Dict[str, List[str]] = {
            name: [f"[{name}] aguardando…", "", "", "", ""] for name in self.worker_names
        }
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._running = False

    def set_lines(self, worker: str, lines: List[str]):
        with self._lock:
            if worker in self.state:
                padded = (lines + [""] * 5)[:5]
                self.state[worker] = [l[:180] for l in padded]

    def start(self):
        self._running = True
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _render(self):
        with self._lock:
            blocks = [self.state.get(name, ["", "", "", "", ""]) for name in self.worker_names]
        out = [ANSI_CLEAR]
        for i, blk in enumerate(blocks):
            out.extend(blk)
            if i < len(blocks) - 1:
                out.append(SEP)
        sys.stdout.write("\n".join(out))
        sys.stdout.flush()

    def _loop(self):
        while self._running and not CANCELLED:
            self._render()
            time.sleep(self.interval)

# ===================== FFmpeg (progresso + log) =====================
def build_ffmpeg_cmd(
    src: Path,
    dst: Path,
    use_nvenc: bool,
    meta: Dict,
    target_w: int,
    target_h: int,
    maxrate: str,
    bufsize: str,
    cq_or_quality: int,
    decoder_threads: Optional[int] = None,
    filter_threads: Optional[int] = None,
    gpu_decode: bool = False,
    use_scale_cuda: bool = False,
) -> List[str]:

    if use_nvenc and gpu_decode and use_scale_cuda:
        vf_chain = [f"scale_cuda={target_w}:{target_h}:force_original_aspect_ratio=decrease"]
    else:
        vf_chain = [f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:force_divisible_by=2", "setsar=1"]

    if use_nvenc:
        vopts = [
            "-c:v", VIDEO_CODEC_GPU,
            "-preset", "p5",
            "-rc:v", "vbr_hq",
            "-cq:v", str(cq_or_quality),
            "-b:v", "0",
            "-profile:v", VIDEO_PROFILE,
            "-level:v", VIDEO_LEVEL,
            "-pix_fmt", "yuv420p",
        ]
    else:
        vopts = [
            "-c:v", VIDEO_CODEC_CPU,
            "-preset", "slow",
            "-profile:v", VIDEO_PROFILE, "-level:v", VIDEO_LEVEL,
            "-maxrate", maxrate, "-bufsize", bufsize,
            "-crf", str(max(16, min(24, cq_or_quality))),
            "-pix_fmt", "yuv420p",
        ]

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats"]

    if use_nvenc:
        if filter_threads and not (gpu_decode and use_scale_cuda):
            cmd += ["-filter_threads", str(filter_threads), "-filter_complex_threads", str(filter_threads)]
        if gpu_decode and use_scale_cuda:
            cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        elif decoder_threads and decoder_threads > 0:
            cmd += ["-threads", str(decoder_threads)]
    else:
        if decoder_threads and decoder_threads > 0:
            cmd += ["-threads", str(decoder_threads)]

    cmd += ["-i", str(src)]
    cmd += ["-map", "0:v:0", "-vf", ",".join(vf_chain)]
    cmd += vopts

    streams = meta.get("streams", [])
    if any(s.get("codec_type") == "audio" for s in streams):
        cmd += ["-map", "0:a:0", "-c:a:0", AUDIO_CODEC, "-b:a:0", AUDIO_BITRATE, "-ac:a:0", str(AUDIO_CHANNELS)]

    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    text_idx = 0
    for sidx, s in enumerate(sub_streams):
        if is_text_sub(s.get("codec_name", "")):
            cmd += ["-map", f"0:s:{sidx}?", f"-c:s:{text_idx}", "mov_text"]
            text_idx += 1

    cmd += ["-f", TARGET_CONTAINER, "-movflags", "+faststart", str(dst)]
    cmd += ["-progress", "pipe:1"]
    return cmd

def parse_progress_line(line: str, prog: Dict[str, str]):
    if "=" not in line:
        return
    k, v = line.strip().split("=", 1)
    prog[k] = v

def fmt_time(prog: Dict[str, str]) -> str:
    t = prog.get("out_time") or ""
    if not t and "out_time_ms" in prog:
        try:
            ms = int(prog["out_time_ms"])
            s = ms // 1_000_000
            h = s // 3600; s -= h*3600
            m = s // 60; s -= m*60
            t = f"{h:02d}:{m:02d}:{s:02d}"
        except Exception:
            t = ""
    return t

def exec_ffmpeg_with_dashboard(cmd: List[str], worker_name: str, label: str, src_path: Path,
                               dash, log_dir: Path, refresh: float) -> int:
    out_name = Path(cmd[-3]).name if len(cmd) >= 3 else f"{src_path.stem}__{label}.mp4"
    log_name = sanitize_filename(f"{src_path.stem}__{label}.log")
    log_path = log_dir / log_name

    with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
        lf.write("# FFmpeg command:\n" + " ".join(shlex.quote(c) for c in cmd) + "\n\n")
        lf.flush()

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        last_update = 0.0
        prog: Dict[str, str] = {}
        err_tail: List[str] = []

        def drain_stderr():
            try:
                for eline in p.stderr:
                    if not eline:
                        break
                    lf.write("[STDERR] " + eline)
                    lf.flush()
                    el = eline.strip()
                    if el:
                        if len(err_tail) < 3:
                            err_tail.append(el)
                        else:
                            err_tail.pop(0); err_tail.append(el)
            except Exception:
                pass

        t_err = threading.Thread(target=drain_stderr, daemon=True)
        t_err.start()

        header = f"[{worker_name}] {label}"
        dash.set_lines(worker_name, [
            f"{header}",
            f"Arquivo: {src_path.name}",
            f"Saída:   {out_name}",
            "Status: iniciando…",
            ""
        ])

        try:
            for line in p.stdout:
                if CANCELLED:
                    break
                if "=" in line:
                    parse_progress_line(line, prog)
                now = time.time()
                if now - last_update >= max(0.2, min(2.0, refresh)):
                    t = fmt_time(prog) or "--:--:--"
                    fps   = prog.get("fps", "?")
                    spd   = prog.get("speed", "?")
                    sizeb = prog.get("total_size", "")
                    try:
                        sizemb = f"{int(sizeb)/1048576:.1f}MB" if sizeb else ""
                    except Exception:
                        sizemb = ""
                    lines = [
                        f"{header}",
                        f"Arquivo: {src_path.name}",
                        f"t={t}  fps={fps}  speed={spd}  size={sizemb}",
                        f"Saída:   {out_name}",
                        f"Último erro: {err_tail[-1] if err_tail else '(nenhum)'}"
                    ]
                    dash.set_lines(worker_name, lines)
                    last_update = now
            rc = p.wait()
            if rc == 0:
                dash.set_lines(worker_name, [
                    f"{header}",
                    f"Arquivo: {src_path.name}",
                    "Status: concluído.",
                    f"Saída:   {out_name}",
                    f"Log:     {log_path}"
                ])
                lf.write("\n# STATUS: SUCCESS\n")
            else:
                tail = err_tail[-1] if err_tail else "erro desconhecido"
                dash.set_lines(worker_name, [
                    f"{header}",
                    f"Arquivo: {src_path.name}",
                    "Status: FAILED.",
                    f"Saída:   {out_name}",
                    f"Erro:    {tail}"
                ])
                lf.write("\n# STATUS: FAILED\n")
            lf.flush()
            return rc
        finally:
            try:
                p.kill()
            except Exception:
                pass

# ===================== Pipeline de encode (com cascade 720<-1080) =====================
def encode_file_for_targets(
    src_original: Path,
    use_nvenc: bool,
    force: bool,
    per_worker_cpu_threads: Optional[int],
    per_worker_filter_threads: Optional[int],
    gpu_decode: bool,
    use_scale_cuda: bool,
    cascade_720: bool,
    worker_name: str,
    dash: Dashboard,
    log_dir: Path,
    refresh: float,
) -> Tuple[Path, str, int]:

    # processar apenas >1080p
    w0, h0 = ffprobe_size_fast(src_original)
    if not (w0 > 1920 or h0 > 1080):
        dash.set_lines(worker_name, [
            f"[{worker_name}] SKIP",
            f"Arquivo: {src_original.name}",
            f"Resolução: {w0}x{h0} (≤1080p)",
            "Status: skipped",
            ""
        ])
        return (src_original, "skipped(resolution<=1080p)", 0)

    statuses = []
    codes = []

    # caminho potencial da 1080p (usado como fonte para 720p)
    path1080 = make_output_path(src_original, "Optimized-1080p", TARGET_CONTAINER)

    for label, wmax, hmax, maxrate, bufsize, q in TARGETS:
        dst = make_output_path(src_original, label, TARGET_CONTAINER)

        # escolher fonte deste alvo:
        if label == "Optimized-720p" and cascade_720 and path1080.exists():
            src_for_target = path1080  # cascade a partir da 1080p já otimizada
            label_disp = f"{label} [src=1080p]"
        else:
            src_for_target = src_original
            label_disp = f"{label} [src=orig]"

        # se saída existe e não forçar, pular
        if dst.exists() and not force:
            dash.set_lines(worker_name, [
                f"[{worker_name}] {label_disp}",
                f"Arquivo: {src_for_target.name}",
                "Status: skipped(exists)",
                f"Saída:   {dst.name}",
                ""
            ])
            statuses.append(f"{label}: skipped(exists)")
            codes.append(0)
            continue

        # ffprobe da fonte escolhida (pode ser 1080p otimizada)
        try:
            meta = ffprobe_meta(src_for_target)
        except Exception as e:
            dash.set_lines(worker_name, [
                f"[{worker_name}] {label_disp}",
                f"Arquivo: {src_for_target.name}",
                f"ffprobe: {e}",
                "Status: failed(ffprobe)",
                ""
            ])
            statuses.append(f"{label}: failed(ffprobe)")
            codes.append(1)
            continue

        # 1) tentar GPU
        cmd_gpu = None
        try:
            cmd_gpu = build_ffmpeg_cmd(
                src=src_for_target, dst=dst, use_nvenc=use_nvenc, meta=meta,
                target_w=wmax, target_h=hmax, maxrate=maxrate, bufsize=bufsize, cq_or_quality=q,
                decoder_threads=per_worker_cpu_threads if not (gpu_decode and use_scale_cuda) else None,
                filter_threads=per_worker_filter_threads if not (gpu_decode and use_scale_cuda) else None,
                gpu_decode=gpu_decode and use_scale_cuda,
                use_scale_cuda=use_scale_cuda
            )
        except Exception:
            cmd_gpu = None

        rc_gpu = -1
        if use_nvenc and cmd_gpu:
            rc_gpu = exec_ffmpeg_with_dashboard(cmd_gpu, worker_name, label_disp, src_for_target, dash, log_dir, refresh)

        ok_gpu = (rc_gpu == 0 and dst.exists() and dst.stat().st_size > 0)

        if ok_gpu:
            statuses.append(f"{label}: encoded(GPU)")
            codes.append(0)
        else:
            # limpar 0B se houver
            if dst.exists() and dst.stat().st_size == 0:
                try: dst.unlink()
                except Exception: pass
            # 2) fallback CPU
            try:
                cmd_cpu = build_ffmpeg_cmd(
                    src=src_for_target, dst=dst, use_nvenc=False, meta=meta,
                    target_w=wmax, target_h=hmax, maxrate=maxrate, bufsize=bufsize, cq_or_quality=q,
                    decoder_threads=DEFAULT_CPU_THREADS_CPUWORKER,
                    filter_threads=None,
                    gpu_decode=False, use_scale_cuda=False
                )
            except Exception as e:
                dash.set_lines(worker_name, [
                    f"[{worker_name}] {label_disp}",
                    f"Arquivo: {src_for_target.name}",
                    f"Erro build CPU: {e}",
                    "Status: failed(build)",
                    ""
                ])
                statuses.append(f"{label}: failed(build-cpu): {e}")
                codes.append(1)
                continue

            dash.set_lines(worker_name, [
                f"[{worker_name}] {label_disp}",
                f"Arquivo: {src_for_target.name}",
                "Status: fallback-to-CPU",
                f"Saída:   {dst.name}",
                ""
            ])
            rc_cpu = exec_ffmpeg_with_dashboard(cmd_cpu, worker_name, f"{label_disp}-CPU", src_for_target, dash, log_dir, refresh)
            if rc_cpu == 0 and dst.exists() and dst.stat().st_size > 0:
                statuses.append(f"{label}: encoded(CPU-fallback)")
                codes.append(0)
            else:
                statuses.append(f"{label}: failed(ffmpeg rc={rc_cpu})")
                codes.append(rc_cpu or 1)

        if CANCELLED:
            break

        # após sucesso da 1080p, garantir que 720p usará a 1080p como fonte
        if label == "Optimized-1080p" and cascade_720 and (dst.exists() and dst.stat().st_size > 0):
            path1080 = dst  # confirmar caminho final

    worst = max(codes) if codes else 1
    return (src_original, " | ".join(statuses) if statuses else "no-op", worst)

def worker_loop(name: str, q: "queue.Queue[Path]", use_nvenc: bool, force: bool,
                per_worker_cpu_threads: Optional[int], per_worker_filter_threads: Optional[int],
                gpu_decode: bool, use_scale_cuda: bool, cascade_720: bool,
                dash: Dashboard, log_dir: Path, refresh: float):
    while not CANCELLED:
        try:
            src = q.get_nowait()
        except queue.Empty:
            dash.set_lines(name, [f"[{name}] idle.", "", "", "", ""])
            return
        try:
            dash.set_lines(name, [f"[{name}] preparando…", f"Arquivo: {src.name}", "", "", ""])
            encode_file_for_targets(
                src, use_nvenc, force,
                per_worker_cpu_threads, per_worker_filter_threads,
                gpu_decode, use_scale_cuda, cascade_720,
                name, dash, log_dir, refresh
            )
        finally:
            q.task_done()

# ===================== Main =====================
def main():
    parser = argparse.ArgumentParser(
        description="2×GPU ou 1×GPU com orçamento de CPU para decode/scale. Painel 3×5, logs, fallback. 720p pode cascatear da 1080p."
    )
    parser.add_argument("root", type=str, help="Diretório raiz da biblioteca.")
    parser.add_argument("--force", action="store_true", help="Sobrescrever versões existentes.")
    parser.add_argument("--gpu-workers", type=int, default=DEFAULT_GPU_WORKERS, help="Workers GPU (1 ou 2).")
    parser.add_argument("--cpu-workers", type=int, default=DEFAULT_CPU_WORKERS, help="Workers CPU (libx264).")
    parser.add_argument("--cpu-threads", type=int, default=DEFAULT_CPU_THREADS_CPUWORKER, help="Threads por worker CPU (se usado).")
    parser.add_argument("--gpu-filter-threads", type=int, default=DEFAULT_FILTER_THREADS_GPU, help="Threads de filtros nos jobs GPU (quando scale no CPU).")
    parser.add_argument("--cpu-budget-for-gpu", type=int, default=DEFAULT_CPU_BUDGET_FOR_GPU, help="Orçamento TOTAL de threads de CPU para decode/scale dos jobs GPU.")
    parser.add_argument("--gpu-decode", action="store_true", help="Tentar NVDEC + scale_cuda (se existir).")
    parser.add_argument("--refresh", type=float, default=1.0, help="Atualização do painel (0.2–2.0 s).")
    parser.add_argument("--log-dir", type=str, default="encode-logs", help="Diretório de logs.")
    parser.add_argument("--no-cascade-720", action="store_true", help="Não gerar 720p a partir da 1080p otimizada; usar sempre o arquivo original.")
    args = parser.parse_args()

    if not has_ffmpeg():
        print("ffmpeg/ffprobe não encontrados no PATH.", file=sys.stderr)
        sys.exit(2)

    log_dir = Path(args.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Logs: {log_dir}")

    nvenc_ok = has_nvenc()
    if not nvenc_ok and args.gpu_workers > 0:
        print("[AVISO] NVENC não encontrado; workers GPU cairão para CPU.", file=sys.stderr)

    use_scale_cuda = False
    if args.gpu_decode and nvenc_ok:
        use_scale_cuda = has_filter("scale_cuda")
        if not use_scale_cuda:
            print("[INFO] 'scale_cuda' indisponível; manter decode/scale no CPU com orçamento de threads.")

    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"Diretório inválido: {root}", file=sys.stderr)
        sys.exit(2)

    # candidatar apenas >1080p
    all_files = iter_video_files(root)
    candidates: List[Path] = []
    skipped_small = 0
    for f in all_files:
        w, h = ffprobe_size_fast(f)
        if w > 1920 or h > 1080:
            candidates.append(f)
        else:
            skipped_small += 1

    print(f"[INFO] Candidatos (>1080p): {len(candidates)} | Ignorados (≤1080p): {skipped_small}")
    if not candidates:
        print("Nenhum arquivo com resolução >1080p encontrado.")
        sys.exit(0)

    # painel: até 2 GPU + até 1 CPU (3 blocos)
    gpu_workers = min(2, max(0, args.gpu_workers))
    cpu_workers = min(1, max(0, args.cpu_workers))

    # dividir orçamento de CPU pelos workers GPU quando decode/scale no CPU
    per_worker_cpu_threads = None
    per_worker_filter_threads = None
    if gpu_workers > 0 and not (args.gpu_decode and use_scale_cuda):
        total = max(1, args.cpu_budget_for_gpu)
        per_worker_cpu_threads = max(1, floor(total / gpu_workers))
        per_worker_filter_threads = per_worker_cpu_threads
    else:
        per_worker_cpu_threads = None
        per_worker_filter_threads = args.gpu_filter_threads

    # workers
    worker_names: List[str] = []
    for i in range(gpu_workers):
        worker_names.append(f"GPU#{i+1}")
    for i in range(cpu_workers):
        worker_names.append(f"CPU#{i+1}")
    while len(worker_names) < 3:
        worker_names.append(f"IDLE#{len(worker_names)+1}")

    enable_ansi_on_windows()
    dash = Dashboard(worker_names=worker_names, interval=max(0.2, min(2.0, args.refresh)))
    dash.start()

    q_files: "queue.Queue[Path]" = queue.Queue()
    for f in candidates:
        q_files.put(f)

    threads: List[threading.Thread] = []

    for i in range(gpu_workers):
        t = threading.Thread(
            target=worker_loop,
            args=(f"GPU#{i+1}", q_files, nvenc_ok, args.force,
                  per_worker_cpu_threads, per_worker_filter_threads,
                  args.gpu_decode, use_scale_cuda, not args.no_cascade_720,
                  dash, log_dir, args.refresh),
            daemon=True
        )
        threads.append(t)

    for i in range(cpu_workers):
        t = threading.Thread(
            target=worker_loop,
            args=(f"CPU#{i+1}", q_files, False, args.force,
                  args.cpu_threads, None,
                  False, False, not args.no_cascade_720,
                  dash, log_dir, args.refresh),
            daemon=True
        )
        threads.append(t)

    for t in threads:
        t.start()

    try:
        q_files.join()
    except KeyboardInterrupt:
        pass
    finally:
        dash.stop()

    sys.stdout.write(ANSI_CLEAR + "[GPU#1] pronto.\n[GPU#2] pronto.\n[CPU#1] pronto.\n")
    sys.stdout.flush()

if __name__ == "__main__":
    main()
