import json
from pathlib import Path

try:
    from graphify.cache import check_semantic_cache
    files = [
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\_critical_schema.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\scoring\layer_liquidity.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\scoring\layer_momentum.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\scoring\layer_order_flow.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\scoring\layer_structure.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\scoring\layer_volatility.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\scoring\futures_pipeline_scorer.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\services\feature_engine.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\services\indicators_provider.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\services\order_flow_service.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\services\score_engine.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\tasks\compute_indicators.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\tasks\pipeline_scan.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\tasks\collect_market_data.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\utils\indicator_merge.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\services\robust_indicators\compute.py',
        r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\services\robust_indicators\score.py',
    ]
    cached_nodes, cached_edges, cached_hyperedges, uncached = check_semantic_cache(files)
    Path(r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\graphify-out\.graphify_cached.json').write_text(
        json.dumps({'nodes': cached_nodes, 'edges': cached_edges, 'hyperedges': cached_hyperedges}, ensure_ascii=False), encoding='utf-8')
    print(f'Cache: {len(files)-len(uncached)} hit, {len(uncached)} need extraction')
    for f in uncached:
        print(f'UNCACHED: {f}')
except Exception as e:
    print(f'Cache check error: {e}')
    # Write empty cache
    Path(r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\graphify-out\.graphify_cached.json').write_text(
        json.dumps({'nodes': [], 'edges': [], 'hyperedges': []}, ensure_ascii=False), encoding='utf-8')
