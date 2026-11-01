"""Copy measured quality reports into one platform results location with source hashes."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/model-quality'
SOURCES={
 'batch.json':('batch/reports/metrics.json','Retained local batch evaluation from the imported source history.'),
 'sms-qwen05.json':('text-risk/results/eval_qwen0.5b.json','Retained paired400-message SMS evaluation; hybrid routing is replayed.'),
 'sms-qwen15.json':('text-risk/results/eval_qwen1.5b.json','Retained paired400-message SMS evaluation; hybrid routing is replayed.'),
 'graph.json':('research/graph/results/metrics.json','Fresh integrated full26-fit CUDA/CPU run, both feature sets,3seeds.'),
}
OUT.mkdir(parents=True,exist_ok=True)
manifest=[]
for name,(source,scope) in SOURCES.items():
    data=(ROOT/source).read_bytes();json.loads(data)
    (OUT/name).write_bytes(data)
    manifest.append({'result':name,'source':source,'sha256':hashlib.sha256(data).hexdigest(),'measurement_scope':scope})
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n', encoding='utf-8', newline='\n')
print('Four original measured JSON reports copied byte-for-byte with provenance hashes.')
