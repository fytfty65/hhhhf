"""Compress rendered originals; never invent missing seasonal assets."""
from pathlib import Path
from PIL import Image
import json

root = Path(__file__).resolve().parents[2]
source = root / 'work/seasonal-art'
target = root / 'front/public/hero/v1'
target.mkdir(parents=True, exist_ok=True)
manifest = {'version': 'v1', 'source': 'Original procedural Blender scene, seed 127', 'assets': []}
for season in ('spring', 'summer', 'autumn', 'winter'):
    for view in ('desktop', 'mobile'):
        name = f'{season}-{view}'
        path = source / f'{name}.png'
        if not path.exists():
            print('MISSING', path)
            continue
        with Image.open(path) as original:
            image = original.convert('RGB')
            output = target / f'{name}.webp'
            image.save(output, 'WEBP', quality=86, method=6)
            manifest['assets'].append({'season': season, 'view': view, 'url': f'/hero/v1/{name}.webp', 'width': image.width, 'height': image.height, 'bytes': output.stat().st_size, 'fallback': 'inline PaintedLandscape SVG'})
            print(output.name, output.stat().st_size)
        for layer in ('background', 'subject'):
            layer_source = source / f'{name}-{layer}.png'
            if not layer_source.exists():
                continue
            with Image.open(layer_source) as original:
                image = original.convert('RGBA' if layer == 'subject' else 'RGB')
                output = target / f'{name}-{layer}.webp'
                image.save(output, 'WEBP', quality=88, method=6)
                manifest.setdefault('layers', []).append({'season': season, 'view': view, 'layer': layer, 'url': f'/hero/v1/{output.name}', 'width': image.width, 'height': image.height, 'bytes': output.stat().st_size})
                print(output.name, output.stat().st_size)
(target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
if len(manifest['assets']) != 8:
    raise SystemExit('Incomplete seasonal export: require all eight originals before release.')
