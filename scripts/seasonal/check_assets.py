"""Structural artifact QA, explicitly not an artistic quality score."""
from pathlib import Path
from PIL import Image
import json
root=Path(__file__).resolve().parents[2]
folder=root/'work/seasonal-art'
checks=[]
for season in ('spring','summer','autumn','winter'):
    for view,size in (('desktop',(1920,1080)),('mobile',(1080,1920))):
        for layer in ('','-background','-subject'):
            path=folder/f'{season}-{view}{layer}.png'
            row={'file':path.name,'exists':path.exists()}
            if path.exists():
                with Image.open(path) as im:
                    im.load()
                    row.update(size=list(im.size),size_ok=im.size==size,mode=im.mode,bytes=path.stat().st_size)
                    if layer=='-subject':
                        alpha=im.getchannel('A') if im.mode=='RGBA' else None
                        row['alpha_extrema']=alpha.getextrema() if alpha else None
                        row['alpha_ok']=bool(alpha and alpha.getextrema()==(0,255))
                        row['subject_bbox']=alpha.getbbox() if alpha else None
            checks.append(row)
report={'checks':checks,'complete':all(r.get('size_ok') and r.get('alpha_ok',True) for r in checks),'note':'Checks file decoding, dimensions and alpha only; does not evaluate visual fidelity.'}
(folder/'asset-checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
if not report['complete']:raise SystemExit(1)
