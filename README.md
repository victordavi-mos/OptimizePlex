# OptmizePlex
Little python/ffmpeg script to optmize 4k videos into 1080p/720p used by plex on my Pi4
otimizaplex_dashboard_hybrid.py — transcoder em lote com painel “3×5”, logs e versões Plex (1080p/720p), com fallback e cascade 720p←1080p.
SYNOPSIS

py .\otimizaplex_dashboard_hybrid.py ROOT [OPÇÕES]

DESCRIPTION

Varre ROOT recursivamente e processa apenas vídeos >1080p (largura >1920 ou altura >1080), gerando até duas versões compatíveis com Plex, dentro de Plex Versions/:

    Optimized-1080p (H.264 + AAC 2.0, MP4)
    Optimized-720p (H.264 + AAC 2.0, MP4)

Recursos:

    1×GPU ou 2×GPU (NVENC) com orçamento de CPU reservado para decode/scale dos jobs GPU.
    Cascade 720p ← 1080p: quando habilitado (padrão), a 720p é gerada a partir da 1080p já otimizada.
    Fallback automático: falhando NVENC, recodifica no CPU (libx264).
    Painel 3×5 linhas (um bloco por worker; máx. 2×GPU + 1×CPU).
    Logs por conversão em encode-logs/.

Mapeamentos:

    Áudio: 1ª trilha → AAC estéreo 192 kbps.
    Legendas texto: convertidas para mov_text (SRT/ASS/SSA/WebVTT). Legendas imagem (PGS/DVB) não são incluídas.

Política de sobrescrita: saídas existentes são puladas. Use --force para recriar.
REQUIREMENTS

    Windows com PowerShell.
    ffmpeg e ffprobe no PATH.
    NVENC disponível (para workers GPU).
    Opcional: filtro scale_cuda no FFmpeg para --gpu-decode.

OPERATION

    Localiza vídeos suportados (ex.: .mkv, .mp4, …), exclui Plex Versions/ e arquivos já “(Optimized-…)”.
    Filtra >1080p.
    Para cada título:
        Gera 1080p; depois 720p.
        Se cascade ativo e a 1080p existir/concluir, a 720p usa a 1080p como fonte.
        Tenta NVENC; se falhar/gerar 0 B, faz fallback CPU.
    Exibe progresso por worker (5 linhas) e grava log.

Saída: Pasta do filme\Plex Versions\Nome (Optimized-1080p).mp4 e …(Optimized-720p).mp4.
OPTIONS

--force
    Recria as saídas mesmo que existam.
--gpu-workers N
    Número de workers GPU (1 ou 2). Padrão: 2.
--cpu-workers N
    Worker CPU (0 ou 1). Padrão: 0.
--cpu-threads N
    Apenas para o worker CPU (quando --cpu-workers 1). Fallback CPU interno usa 5.
--cpu-budget-for-gpu N
    Orçamento total de threads de CPU para decode/scale dos jobs GPU quando o scale roda no CPU (sem scale_cuda). O valor é dividido por worker (ex.: N=10, --gpu-workers 2 ⇒ 5 por job). Padrão: 10.
--gpu-filter-threads N
    Threads de filtros nos jobs GPU quando o scale roda no CPU (sem scale_cuda). Usado como mínimo quando não houver orçamento calculado.
--gpu-decode
    Tenta NVDEC + scale_cuda (se disponível). Sem scale_cuda, a cadeia fica no CPU.
--refresh SECS
    Intervalo de atualização do painel (0.2–2.0). Padrão: 1.0.
--log-dir PATH
    Diretório de logs. Padrão: encode-logs.
--no-cascade-720
    Desativa o cascade; 720p sempre parte do arquivo original.

DASHBOARD

Três blocos (máx.): GPU#1, GPU#2, CPU#1. Cada bloco mostra 5 linhas:

    Worker e label do alvo (ex.: Optimized-1080p / Optimized-720p [src=1080p])
    Nome do arquivo de entrada
    t=… fps=… speed=… size=…
    Nome do arquivo de saída
    Último erro do stderr (se houver) ou “(nenhum)”

LOGS

Para cada saída gerada: encode-logs/<TÍTULO>__<ALVO>.log

Contém:

    Linha de comando do FFmpeg usada
    Progresso (-progress)
    stderr do FFmpeg
    STATUS: SUCCESS/FAILED

EXIT STATUS

    0: execução concluída (pode haver arquivos falhados; ver logs).
    2: erro de uso/ambiente (FFmpeg ausente, diretório inválido, etc.).

FILES

    Plex Versions/ — diretório das versões otimizadas por título.
    encode-logs/ — diretório de logs por conversão.

NOTES

    Apenas >1080p são processados.
    Saídas 0 B são removidas e reprocessadas via fallback CPU.
    Em pipeline GPU completo (--gpu-decode com scale_cuda), o orçamento de CPU tem pouco efeito.
    Desempenho depende de I/O do disco e do preset NVENC (p5 no script).

EXAMPLES
1) 2×GPU, 10 threads de CPU para decode/scale dos jobs GPU, cascade ativo

py .\otimizaplex_dashboard_hybrid.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10

2) 1×GPU, 10 threads de CPU para decode/scale, cascade ativo

py .\otimizaplex_dashboard_hybrid.py "E:\" --gpu-workers 1 --cpu-workers 0 --cpu-budget-for-gpu 10

3) 2×GPU com tentativa de NVDEC + scale_cuda (se disponível)

py .\otimizaplex_dashboard_hybrid.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --gpu-decode

4) 2×GPU, desativando o cascade (720p direto do original)

py .\otimizaplex_dashboard_hybrid.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --no-cascade-720

5) Forçar recriação das saídas existentes

py .\otimizaplex_dashboard_hybrid.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --force

6) Ajustar taxa de atualização do painel e pasta de logs

py .\otimizaplex_dashboard_hybrid.py "E:\" --gpu-workers 2 --cpu-workers 0 --cpu-budget-for-gpu 10 --refresh 0.5 --log-dir "E:\logs-plex"

7) Somente CPU (diagnóstico)

py .\otimizaplex_dashboard_hybrid.py "E:\" --gpu-workers 0 --cpu-workers 1 --cpu-threads 6


