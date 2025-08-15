Pequeno script em Python/FFmpeg para otimizar vídeos 4K para 1080p/720p usados pelo Plex no meu Pi4.
# OptimizePlexVideos.py

Transcoder em lote com painel 3×5, logs e versões Plex (1080p/720p), com *fallback* e *cascade* 720p ← 1080p.

---

## Sumário

- [Descrição](#descrição)
- [Requisitos](#requisitos)
- [Operação](#operação)
- [Opções](#opções)
- [Dashboard](#dashboard)
- [Logs](#logs)
- [Códigos de saída](#códigos-de-saída)
- [Notas](#notas)
- [Exemplos](#exemplos)

---

## Descrição

Varre o diretório raiz recursivamente e processa **apenas vídeos >1080p** (largura >1920 **ou** altura >1080), gerando versões compatíveis com o Plex em `Plex Versions/`:

- **Optimized-1080p** — H.264 + AAC 2.0 (MP4)
- **Optimized-720p** — H.264 + AAC 2.0 (MP4)

Recursos:

- **1× ou 2× GPU (NVENC)** com **orçamento de CPU** reservado para **decode/scale** dos *jobs* GPU.
- **Cascade 720p ← 1080p**: 720p é gerada a partir da 1080p já otimizada (padrão).
- **Fallback automático**: falhando NVENC, recodifica no **CPU** (libx264).
- **Painel 3×5 linhas** (até 2×GPU + 1×CPU).
- **Logs por conversão** em `encode-logs/`.

Mapeamentos de trilhas:

- **Áudio**: primeira trilha → AAC estéreo 192 kbps.
- **Legendas texto**: convertidas para `mov_text` (SRT/ASS/SSA/WebVTT).  
  Legendas imagem (PGS/DVB) **não** são incluídas.

Política de sobrescrita: saídas existentes são **puladas**. Use `--force` para recriar.

---

## Requisitos

- Windows com PowerShell.
- `ffmpeg` e `ffprobe` no `PATH`.
- NVENC disponível (para workers GPU).
- Opcional: filtro `scale_cuda` no FFmpeg para `--gpu-decode`.

---

## Operação

1. Localiza vídeos suportados (ex.: `.mkv`, `.mp4`), ignora `Plex Versions/` e arquivos já “(Optimized-…)`.
2. Filtra apenas **>1080p**.
3. Para cada título:
   - Gera **1080p**, depois **720p**.
   - Se **cascade** ativo e a 1080p existir/concluir, a **720p usa a 1080p como fonte**.
   - Tenta **NVENC**; se falhar/gerar 0 B, faz **fallback CPU**.
4. Exibe progresso por worker (5 linhas) e grava log.

Saídas em:
```
<Filme>\Plex Versions\<Nome> (Optimized-1080p).mp4
<Filme>\Plex Versions\<Nome> (Optimized-720p).mp4
```

---

## Opções

```
py .\OptimizePlexVideos.py ROOT [OPÇÕES]
```

- `--force`  
  Recria as saídas mesmo que existam.

- `--gpu-workers N`  
  Workers GPU (1 ou 2). Padrão: 2.

- `--cpu-workers N`  
  Worker CPU (0 ou 1). Padrão: 0.

- `--cpu-threads N`  
  Threads por **worker CPU** (somente se `--cpu-workers 1`). Fallback interno usa 5.

- `--cpu-budget-for-gpu N`  
  **Orçamento TOTAL** de threads de CPU para **decode/scale** dos *jobs* GPU quando o *scale* roda no **CPU** (sem `scale_cuda`).  
  O valor é **dividido por worker** (ex.: 10 com `--gpu-workers 2` ⇒ 5 por job). Padrão: 10.

- `--gpu-filter-threads N`  
  Threads de filtros nos *jobs* GPU quando o *scale* roda no **CPU** (sem `scale_cuda`). Usado como mínimo quando não houver orçamento calculado.

- `--gpu-decode`  
  Tenta **NVDEC + `scale_cuda`** (se disponível). Sem `scale_cuda`, decode/scale permanece no **CPU**.

- `--refresh SECS`  
  Intervalo de atualização do painel (0.2–2.0). Padrão: 1.0.

- `--log-dir PATH`  
  Diretório de logs. Padrão: `encode-logs`.

- `--no-cascade-720`  
  Desativa o *cascade*; 720p sempre parte do arquivo **original**.

---

## Dashboard

Três blocos (máx.): `GPU#1`, `GPU#2`, `CPU#1`. Cada bloco mostra 5 linhas:

1) Worker e alvo (ex.: `Optimized-1080p` / `Optimized-720p [src=1080p]`)  
2) Arquivo de entrada  
3) `t=…  fps=…  speed=…  size=…`  
4) Arquivo de saída  
5) Última linha do `stderr` ou “(nenhum)”

---

## Logs

Para cada saída gerada:  
`encode-logs/<TITULO>__<ALVO>.log`

Contém:

- Linha de comando do FFmpeg usada
- Progresso (`-progress`)
- `stderr` do FFmpeg
- `STATUS: SUCCESS/FAILED`

---

## Códigos de saída

- **0** — execução concluída (pode haver arquivos falhados; ver logs).  
- **2** — erro de uso/ambiente (FFmpeg ausente, diretório inválido, etc.).

---

## Notas

- Apenas **>1080p** são processados.  
- Saídas **0 B** são removidas e reprocessadas via fallback CPU.  
- Com `--gpu-decode` **e** `scale_cuda`, o orçamento de CPU tem pouco efeito.  
- Desempenho depende de I/O e do *preset* NVENC (padrão `p5` no script).

---

## Exemplos

> Nos exemplos abaixo, use o diretório raiz `E:\`.

### 1) 2×GPU, 10 threads de CPU para decode/scale dos jobs GPU, *cascade* ativo
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10
```

### 2) 1×GPU, 10 threads de CPU para decode/scale, *cascade* ativo
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 1 --cpu-workers 0 --cpu-budget-for-gpu 10
```

### 3) 2×GPU com tentativa de NVDEC + scale_cuda (se disponível)
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --gpu-decode
```

### 4) 2×GPU, desativando o *cascade* (720p direto do original)
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --no-cascade-720
```

### 5) Recriar saídas existentes
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --force
```

### 6) Ajustar taxa de atualização do painel e pasta de logs
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --refresh 0.5 --log-dir "E:\logs-plex"
```

### 7) Somente CPU (diagnóstico)
```powershell
py .\OptimizePlexVideos.py "E:\" --gpu-workers 0 --cpu-workers 1 --cpu-threads 6
```
