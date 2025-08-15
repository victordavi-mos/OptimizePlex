Little python/ffmpeg script to optimize 4k videos into 1080p/720p used by plex on my Pi4 
# OptimizePlexVideos.py

Batch transcoder with a 3×5 dashboard, logs, and Plex-ready versions (1080p/720p), with automatic fallback and a 720p-from-1080p cascade.

---

## Table of Contents

- [Description](#description)
- [Requirements](#requirements)
- [Operation](#operation)
- [Options](#options)
- [Dashboard](#dashboard)
- [Logs](#logs)
- [Exit Codes](#exit-codes)
- [Notes](#notes)
- [Examples](#examples)

---

## Description

Recursively scans the root directory and processes **only videos >1080p** (width >1920 **or** height >1080), generating Plex-compatible versions in `Plex Versions/`:

- **Optimized-1080p** — H.264 + AAC 2.0 (MP4)  
- **Optimized-720p** — H.264 + AAC 2.0 (MP4)

Features:

- **1× or 2× GPU (NVENC)** with a **CPU budget** reserved for **decode/scale** of GPU jobs.
- **Cascade 720p ← 1080p**: 720p is generated from the already optimized 1080p (default).
- **Automatic fallback**: if NVENC fails or produces a 0‑byte file, it recodes on **CPU** (libx264).
- **3×5-line dashboard** (up to 2×GPU + 1×CPU).
- **Per-conversion logs** in `encode-logs/`.

Track handling:

- **Audio**: first audio track → AAC stereo 192 kbps.
- **Text subtitles**: converted to `mov_text` (SRT/ASS/SSA/WebVTT).  
  Image subtitles (PGS/DVB) are **not** included.

Overwrite policy: existing outputs are **skipped**. Use `--force` to recreate.

---

## Requirements

- Windows with PowerShell.  
- `ffmpeg` and `ffprobe` in `PATH`.  
- NVENC available (for GPU workers).  
- Optional: `scale_cuda` filter in FFmpeg for `--gpu-decode`.

---

## Operation

1. Locates supported videos (e.g., `.mkv`, `.mp4`), ignores `Plex Versions/` and files already marked “(Optimized-…)”.  
2. Filters **>1080p** only.  
3. For each title:  
   - Produces **1080p**, then **720p**.  
   - If **cascade** is enabled and 1080p exists/completes, **720p uses the 1080p file as source**.  
   - Tries **NVENC**; if it fails/produces 0 B, uses **CPU fallback**.  
4. Shows per-worker progress (5 lines) and writes a log.

Outputs are saved to:
```
<Movie>\Plex Versions\<Name> (Optimized-1080p).mp4
<Movie>\Plex Versions\<Name> (Optimized-720p).mp4
```

---

## Options

```
py .\OptimizePlexVideos.py ROOT [OPTIONS]
```

- `--force`  
  Recreate outputs even if they already exist.

- `--gpu-workers N`  
  Number of GPU workers (1 or 2). Default: 2.

- `--cpu-workers N`  
  CPU worker (0 or 1). Default: 0.

- `--cpu-threads N`  
  Threads per **CPU worker** (only if `--cpu-workers 1`). Internal CPU fallback uses 5.

- `--cpu-budget-for-gpu N`  
  **Total** CPU threads reserved for **decode/scale** of GPU jobs when scaling runs on the **CPU** (no `scale_cuda`).  
  Split **per worker** (e.g., 10 with `--gpu-workers 2` ⇒ 5 per job). Default: 10.

- `--gpu-filter-threads N`  
  Filter threads on GPU jobs when scaling runs on the **CPU** (no `scale_cuda`). Used as a minimum when no per‑worker budget applies.

- `--gpu-decode`  
  Attempt **NVDEC + `scale_cuda`** (if available). Without `scale_cuda`, decode/scale remains on **CPU**.

- `--refresh SECS`  
  Dashboard refresh interval (0.2–2.0). Default: 1.0.

- `--log-dir PATH`  
  Logs directory. Default: `encode-logs`.

- `--no-cascade-720`  
  Disable cascade; 720p always uses the **original** file.

---

## Dashboard

Three blocks (max.): `GPU#1`, `GPU#2`, `CPU#1`. Each block shows 5 lines:

1) Worker and target (e.g., `Optimized-1080p` / `Optimized-720p [src=1080p]`)  
2) Input file  
3) `t=…  fps=…  speed=…  size=…`  
4) Output file  
5) Last `stderr` line or “(none)”

---

## Logs

For each produced output:  
`encode-logs/<TITLE>__<TARGET>.log`

Contains:

- FFmpeg command line used  
- Progress (`-progress`)  
- FFmpeg `stderr`  
- `STATUS: SUCCESS/FAILED`

---

## Exit Codes

- **0** — finished (some files may have failed; check logs).  
- **2** — usage/environment error (FFmpeg missing, invalid directory, etc.).

---

## Notes

- Only **>1080p** videos are processed.  
- **0‑byte** outputs are removed and reprocessed via CPU fallback.  
- With `--gpu-decode` **and** `scale_cuda`, the CPU budget has little effect.  
- Throughput depends on disk I/O and NVENC preset (default `p5` in the script).

---

## Examples

> In the examples below, the root directory is `E:\`.

### 1) 2×GPU, 10 CPU threads reserved for GPU jobs’ decode/scale, cascade enabled
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10
```

### 2) 1×GPU, 10 CPU threads for decode/scale, cascade enabled
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 1 --cpu-workers 0 --cpu-budget-for-gpu 10
```

### 3) 2×GPU with NVDEC + scale_cuda attempt (if available)
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --gpu-decode
```

### 4) 2×GPU, cascade disabled (720p from original)
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --no-cascade-720
```

### 5) Force recreation of existing outputs
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --force
```

### 6) Adjust dashboard refresh rate and logs folder
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --refresh 0.5 --log-dir "E:\logs-plex"
```

### 7) CPU-only (diagnostics)
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 0 --cpu-workers 1 --cpu-threads 6
```
