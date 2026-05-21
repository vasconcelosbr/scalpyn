import json
from graphify.detect import detect
from pathlib import Path
result = detect(Path(r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app'))
print(json.dumps(result, ensure_ascii=False))
