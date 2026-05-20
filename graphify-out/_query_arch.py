import json
import networkx as nx
from networkx.readwrite import json_graph
from pathlib import Path
from collections import defaultdict

data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

file_nodes = defaultdict(list)
for nid, nd in G.nodes(data=True):
    sf = nd.get('source_file','')
    if sf:
        file_nodes[sf].append(nd.get('label',''))

dirs = defaultdict(int)
for sf in file_nodes:
    parts = sf.replace('\\', '/').split('/')
    top = '/'.join(parts[:2]) if len(parts) >= 2 else parts[0]
    dirs[top] += 1

print('FILE DISTRIBUTION BY DIR:')
for d, count in sorted(dirs.items(), key=lambda x: -x[1])[:25]:
    print(f'  {count:4d} {d}')

print()
print('GOD NODES + NEIGHBORS:')
degrees = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:8]
for nid, deg in degrees:
    nd = G.nodes[nid]
    label = nd.get('label', nid)
    sf = nd.get('source_file', '')
    print(f'\n  [{deg}] {label} ({sf})')
    for neighbor in list(G.neighbors(nid))[:8]:
        _raw = G[nid][neighbor]
        ed = next(iter(_raw.values()), {}) if isinstance(G, nx.MultiGraph) else _raw
        nl = G.nodes[neighbor].get('label', neighbor)
        rel = ed.get('relation', '')
        conf = ed.get('confidence', '')
        print(f'    --{rel}[{conf}]--> {nl}')
