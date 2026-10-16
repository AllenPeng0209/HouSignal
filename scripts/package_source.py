"""Whitelist source export. Excludes credentials, runtime data and uploads."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'exports/楼响_HouSignal_正式应用源码.zip'
target.parent.mkdir(parents=True, exist_ok=True)
allowed=['apps/api','apps/web','apps/agent-runtime','scripts','infra','specs','tests','docs','vendor',
    'integrations/louxiang-agent/src','integrations/louxiang-agent/plugin/skills']
# Only these hidden plugin descriptors are public source; adjacent local state stays private.
public_descriptors={'.agents/plugins/marketplace.json',
    'integrations/louxiang-agent/plugin/.mcp.json',
    'integrations/louxiang-agent/plugin/.codex-plugin/plugin.json'}
integration_files={'integrations/louxiang-agent/build.py','integrations/louxiang-agent/README.md',
    'integrations/louxiang-agent/plugin/plugin.json','integrations/louxiang-agent/plugin/mcp.json'}
excluded_parts={'node_modules','dist','build','__pycache__','.pytest_cache','.runtime','runtime',
    '.venv','venv','.git','data','databases','uploads','storage','models','modelweights',
    'model_weights','weights','checkpoints'}
excluded_suffixes={'.pyc','.log','.tsbuildinfo','.db','.sqlite','.sqlite3','.safetensors',
    '.bin','.pth','.pt','.ckpt','.onnx','.engine','.trt','.pem','.key','.p12','.pfx',
    '.keystore','.pkl','.pickle','.zip','.tar','.gz','.tgz','.whl','.dmg','.pkg'}


def exportable(path):
    """Only local source and explicitly selected evidence can enter the archive."""
    relative=path.relative_to(ROOT)
    if (not path.is_file() or path.is_symlink()
            or not path.resolve().is_relative_to(ROOT.resolve())): return False
    if any((ROOT/Path(*relative.parts[:index])).is_symlink()
            for index in range(1,len(relative.parts))): return False
    if any(part in excluded_parts for part in relative.parts[:-1]): return False
    if any(part.startswith('.') for part in relative.parts[:-1]) and relative.as_posix() not in public_descriptors: return False
    if (path.name.startswith('.') and path.name not in {'.env.example','.gitignore','.dockerignore'}
            and relative.as_posix() not in public_descriptors): return False
    return path.suffix.lower() not in excluded_suffixes


files=[]
for directory in allowed:
    for path in (ROOT/directory).rglob('*'):
        if exportable(path): files.append(path)
files.extend(ROOT/name for name in sorted(public_descriptors|integration_files) if (ROOT/name).is_file())
files.extend(ROOT/name for name in ['.env.example','.gitignore','.dockerignore','README_IMPLEMENTATION.md','compose.yaml','alembic.ini','pytest.ini'] if (ROOT/name).is_file())
files.extend(ROOT/'reports'/name for name in ['delivery-verification.json','acceptance-flow.json','live-media-check.json','permission-and-identity-check.json','restore-verification.json','final-live-chat.json','openapi.json','production-agent-desktop.jpg','production-agent-mobile.jpg','production-studio-desktop.jpg','production-dashboard.jpg'] if (ROOT/'reports'/name).is_file())
performance_reports=['residential-discovery-verification-20261013.json','residential-discovery-current.png','content-operations-verification-20261013.json','content-operations-skills-desktop.png','content-operations-skills-mobile.png','content-operations-discovery-desktop.png','content-operations-planning-desktop.png','ai-performance-verification.json','queue-baseline.json','ai-model-call-metadata.json','chat-model-evaluation.json','chat-model-evaluation-final.json','content-model-evaluation.json','frontend-perf-fixture.json','frontend-perf-fixture-preview.png','performance-live-agent.jpg','performance-live-stream.jpg']
performance_reports.extend(path.name for path in (ROOT/'reports').glob('ai-latency-*.json'))
performance_reports.extend(['platform-accounts-verification-20261013.json','platform-login-live-20261013.json','platform-accounts-desktop-20261013.jpg','platform-agent-live-20261013.jpg'])
performance_reports.extend(['native-content-migration-verification.json','native-runtime-acceptance.json','native-image-resize-acceptance.png','native-skills-final-desktop.jpg','native-ecommerce-form.jpg','secretary-agent-20261015.json','secretary-integration-verification-20261015.json'])
files.extend(ROOT/'reports'/name for name in performance_reports if (ROOT/'reports'/name).is_file())
acceptance=ROOT/'reports/skill-acceptance'
for path in acceptance.rglob('*'):
    if (path.is_file() and not any(part.startswith('.') for part in path.relative_to(acceptance).parts)
            and path.suffix.lower() in {'.json','.md','.txt','.png','.jpg','.svg','.pdf','.mp4','.wav','.mp3','.srt','.vtt','.html','.csv'}):
        files.append(path)
snapshots=[(str(path.relative_to(ROOT)),path.read_bytes()) for path in sorted(set(files)) if exportable(path)]
manifest={'files':[{'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    for name,raw in snapshots],'excluded':['.env','credentials','uploaded files','database',
    'model weights','installation packages','symlinks','node_modules','.venv']}
fd,temporary=tempfile.mkstemp(dir=target.parent,prefix='.source-export-',suffix='.zip')
os.close(fd)
try:
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in snapshots: archive.writestr(name,raw)
        archive.writestr('source-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    os.replace(temporary,target)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
print(json.dumps({'archive':str(target),'source_files':len(snapshots),'bytes':target.stat().st_size},ensure_ascii=False))
