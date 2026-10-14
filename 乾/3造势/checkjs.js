const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.log('no <script> block'); process.exit(1); }
const src = m[1];
fs.writeFileSync(process.argv[3], src);
try {
  new Function(src);           // syntax check only
  console.log('JS syntax OK, length=' + src.length);
} catch (e) {
  console.log('JS SYNTAX ERROR: ' + e.message);
  // try to locate the line
  const lines = src.split('\n');
  const mm = /line (\d+)/.exec(e.message);
  if (mm) {
    const n = +mm[1];
    for (let i = Math.max(0, n - 4); i < Math.min(lines.length, n + 3); i++)
      console.log((i + 1) + ': ' + lines[i]);
  }
  process.exit(2);
}
