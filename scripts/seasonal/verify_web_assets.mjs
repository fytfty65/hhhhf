import fs from 'node:fs';
import path from 'node:path';
const root = 'front/public/hero/v1';
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
if (manifest.assets.length !== 8) throw Error('Require eight seasonal posters');
let total = 0;
for (const item of [...manifest.assets, ...(manifest.layers || [])]) {
  const filename = path.join('front/public', item.url);
  const bytes = fs.readFileSync(filename);
  if (bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WEBP') throw Error(`Invalid WebP ${filename}`);
  if (bytes.length !== item.bytes) throw Error(`Stale manifest ${filename}`);
  total += bytes.length;
  console.log(item.url, bytes.length);
}
console.log('VERIFIED bytes', total, 'posters', manifest.assets.length, 'layers', manifest.layers?.length || 0);
