"""Bundle the buildless prototype into one offline HTML file."""
from pathlib import Path
import base64, json, re, hashlib, zipfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
assets = {}
for image in sorted((DIST / 'assets').glob('*.jpg')):
    assets['assets/' + image.name] = 'data:image/jpeg;base64,' + base64.b64encode(image.read_bytes()).decode('ascii')
document = (DIST / 'index.html').read_text()
document = document.replace('<link rel="stylesheet" href="styles.css">', '<style>\n' + (DIST / 'styles.css').read_text() + '\n</style>')
document = document.replace('<link rel="stylesheet" href="assistant.css">', '<style>\n' + (DIST / 'assistant.css').read_text() + '\n</style>')
document = document.replace('<script src="model.js"></script>', '<script>window.HSAssets=' + json.dumps(assets) + ';</script>\n<script src="model.js"></script>')
for name in ['model.js','project-store.js','ui.js','views.js','assistant-model.js','assistant-session.js','assistant-view.js','app.js']:
    source = (DIST / name).read_text().replace('</script', '<\\/script')
    document = document.replace('<script src="'+name+'"></script>', '<script>\n' + source + '\n</script>')
target = DIST / '楼响_HouSignal_交互Demo.html'
target.write_text(document)
manifest = {'version':'v0.4.0','single_file':str(target.relative_to(ROOT)),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'embedded_images':list(assets),'external_dependencies':[]}
(ROOT / '交付清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
bundle=ROOT / '楼响_HouSignal_Demo源码.zip'
with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(ROOT.rglob('*')):
        if file.is_file() and file != bundle and '__pycache__' not in file.parts:
            archive.write(file,str(file.relative_to(ROOT)))
print(json.dumps({'html_bytes':target.stat().st_size,'embedded_images':len(assets),'zip_bytes':bundle.stat().st_size,'html':str(target)},ensure_ascii=False))
