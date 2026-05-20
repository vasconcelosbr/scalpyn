import json
import networkx as nx
from networkx.readwrite import json_graph
from pathlib import Path
from collections import defaultdict

data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

# Find nodes for key architectural components
components = [
    'pipeline_scan.py', 'futures_scanner.py', 'spot_engine.py', 'futures_engine.py',
    'score.py', 'compute_scores.py', 'ProfileEngine', 'BlockEngine', 'RuleEngine',
    'celery_app.py', 'spot_scanner.py', 'operational_snapshot.py',
    'futures_position_manager.py', 'futures_emergency.py', 'futures_macro_gate.py',
]
comp_ids = {}
for nid, nd in G.nodes(data=True):
    for c in components:
        if nd.get('label','') == c:
            comp_ids[c] = nid

print('PIPELINE + ENGINE RELATIONSHIPS:')
for c1, n1 in comp_ids.items():
    for neighbor in G.neighbors(n1):
        nl = G.nodes[neighbor].get('label', neighbor)
        _raw = G[n1][neighbor]
        ed = next(iter(_raw.values()), {}) if isinstance(G, nx.MultiGraph) else _raw
        rel = ed.get('relation', '')
        conf = ed.get('confidence', '')
        print(f'  {c1} --{rel}[{conf}]--> {nl}')
    print()
