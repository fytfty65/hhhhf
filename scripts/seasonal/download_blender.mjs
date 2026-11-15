import fs from 'node:fs';
import { open, stat } from 'node:fs/promises';
const file = 'work/seasonal-tools/blender.zip';
const url = 'https://ftp.halifax.rwth-aachen.de/blender/release/Blender4.5/blender-4.5.3-windows-x64.zip';
const size = 400100678;
const prefix = (await stat(file)).size;
const handle = await open(file, 'r+');
let cursor = prefix;
let received = prefix;
const chunk = 8 * 1024 * 1024;
console.log(`Resume verified existing prefix ${prefix}/${size}`);
try {
  await Promise.all(Array.from({ length: 8 }, async () => {
    while (cursor < size) {
      const start = cursor; cursor = Math.min(size, cursor + chunk);
      const end = cursor - 1;
      let done = false;
      for (let attempt = 0; attempt < 4 && !done; attempt++) {
        try {
          const response = await fetch(url, { headers: { Range: `bytes=${start}-${end}` }, signal: AbortSignal.timeout(180000) });
          if (response.status !== 206 || response.headers.get('content-range') !== `bytes ${start}-${end}/${size}`) throw new Error('Range mismatch');
          const bytes = Buffer.from(await response.arrayBuffer());
          if (bytes.length !== end - start + 1) throw new Error('Length mismatch');
          await handle.write(bytes, 0, bytes.length, start);
          received += bytes.length; done = true;
          console.log(`${received}/${size} (${Math.round(received / size * 100)}%)`);
        } catch (e) { if (attempt === 3) throw e; console.error(`Retry range ${start}: ${e.message}`); }
      }
    }
  }));
  fs.writeFileSync('work/seasonal-tools/download-complete.json', JSON.stringify({ size, received }));
  console.log('COMPLETE');
} finally { await handle.close(); }
