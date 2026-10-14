const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const body = html.slice(html.indexOf('<body>'), html.indexOf('</body>'));

const void_ = new Set(['br','hr','img','input','meta','link','area','base','col','embed','source','track','wbr']);
const stack = [];
const re = /<(\/?)([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g;
let m, line = 1, last = 0;
const problems = [];
while ((m = re.exec(body))) {
  line += (body.slice(last, m.index).match(/\n/g) || []).length;
  last = m.index;
  const closing = m[1] === '/', tag = m[2].toLowerCase(), selfClose = m[4] === '/';
  if (void_.has(tag) || selfClose) continue;
  if (!closing) {
    stack.push({ tag, line });
  } else {
    const top = stack.pop();
    if (!top) problems.push(`line ${line}: </${tag}> with no opener`);
    else if (top.tag !== tag) problems.push(`line ${line}: </${tag}> closes <${top.tag}> opened at line ${top.line}`);
  }
}
stack.forEach(s => problems.push(`line ${s.line}: <${s.tag}> never closed`));

console.log(problems.length ? '结构问题:\n  ' + problems.join('\n  ') : '标签闭合 OK');

// where do the sections sit relative to <main>?
const mainOpen = body.indexOf('<main>');
const mainClose = body.indexOf('</main>');
console.log(`\n<main> at ${mainOpen}, </main> at ${mainClose}`);
['s-live','s-brokers','s-routes','s-json','s-test','s-log','wizard'].forEach(id=>{
  const i = body.indexOf(`id="${id}"`);
  console.log(`  ${id.padEnd(10)} at ${String(i).padStart(6)}  ${i>mainOpen && i<mainClose ? '在 main 内' : '在 main 外'}`);
});
