// Regression tests for the rule-library editor.
//
// Bug 1 -- 保存规则后切页，改动丢失
//   saveRule() only queued the write via persistSoon() (700ms debounce), while
//   switching to the 规则库 page runs loadCfg(), which overwrote EDIT with the
//   server's still-stale config; when the timer then fired it wrote that stale
//   config back. Fix: explicit saves persist immediately, and loadCfg() flushes
//   anything still queued before fetching.
//
// Bug 2 -- 填了字段值却存不进去
//   renderFields() bound the value inputs with `rlFields[+inp.dataset.fk]`, but
//   those inputs carry `data-fv`. dataset.fk is undefined, so typing threw and
//   the value never reached rlFields; saveRule() then dropped the field for
//   having an empty value. Fix: read data-fv on the value inputs.
//
// The typing cases below go through the RENDERED inputs (firing oninput), not
// by assigning rlFields directly -- assigning directly would have hidden bug 2.
//
// Run: node check_save.js webui.html
const fs = require('fs');
const path = process.argv[2] || 'webui.html';
const src = fs.readFileSync(path, 'utf8').match(/<script>([\s\S]*)<\/script>/)[1];

// --- minimal DOM shim -------------------------------------------------
function parseInputs(html) {
  const out = [];
  const re = /<input\s+data-(fk|fv)="(\d+)"\s+value="([^"]*)"/g;
  let m;
  while ((m = re.exec(html))) {
    const kind = m[1], idx = m[2];
    out.push({
      kind, idx: +idx,
      value: m[3].replace(/&quot;/g, '"').replace(/&lt;/g, '<')
                 .replace(/&gt;/g, '>').replace(/&amp;/g, '&'),
      // real DOM: a data-fv input has dataset.fv only -- dataset.fk is undefined
      dataset: kind === 'fk' ? { fk: idx } : { fv: idx },
      oninput: null,
    });
  }
  return out;
}

// 规则选择器里的复选框（value=规则名）
function parseChecks(html) {
  const out = [];
  const re = /<input\s+type="checkbox"\s+value="([^"]*)"\s*(checked)?/g;
  let m;
  while ((m = re.exec(html))) {
    out.push({
      type: 'checkbox',
      value: m[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&'),
      checked: !!m[2],
      onchange: null,
      parentElement: { classList: { add() {}, remove() {}, toggle() {} } },
    });
  }
  return out;
}
const store = {};
function el(id) {
  if (store[id]) return store[id];
  if (id === 'rl-fields') {                 // 字段编辑区：要能取到渲染出来的输入框
    const e = {
      id, _html: '', _inputs: [], style: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      set innerHTML(h) { this._html = h; this._inputs = parseInputs(h); },
      get innerHTML() { return this._html; },
      querySelectorAll(sel) {
        const kind = sel.indexOf('fk') >= 0 ? 'fk' : sel.indexOf('fv') >= 0 ? 'fv' : null;
        return kind ? this._inputs.filter(x => x.kind === kind) : [];
      },
      querySelector: () => null,
    };
    store[id] = e;
    return e;
  }
  if (id === 't-rules' || id === 'lt-rules') {   // 规则选择器：要能取到复选框
    const e = {
      id, _html: '', _checks: [], style: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      set innerHTML(h) { this._html = h; this._checks = parseChecks(h); },
      get innerHTML() { return this._html; },
      querySelectorAll(sel) {
        return sel.indexOf('input') >= 0 ? this._checks : [];
      },
      querySelector: () => null,
    };
    store[id] = e;
    return e;
  }
  const e = {
    id, value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
    style: {}, dataset: {}, className: '',
    // classList 要真记状态，否则没法断言「掉线时有没有挂上 show」
    classList: (() => {
      const s = new Set();
      return {
        add: (...c) => c.forEach(x => s.add(x)),
        remove: (...c) => c.forEach(x => s.delete(x)),
        toggle: (c, f) => {
          const on = f === undefined ? !s.has(c) : !!f;
          if (on) s.add(c); else s.delete(c);
          return on;
        },
        contains: c => s.has(c),
      };
    })(),
    focus() {}, appendChild() {},
    querySelectorAll: () => [], querySelector: () => null,
  };
  store[id] = e;
  return e;
}
const document = {
  getElementById: el,
  // 必须和真实的 document.querySelector 一样认 '#'：
  //   '#t-rules' 找 id，'t-rules' 找标签名（找不到返回 null）。
  // 早先这里写成 s.replace(/^#/,'') 自动去 #，比真实浏览器宽松，
  // 结果把「调用方漏写 #」的 bug 藏住了——桩比实现宽松就测不出问题。
  querySelector: s => {
    const t = String(s);
    return t.startsWith('#') ? el(t.slice(1)) : null;
  },
  querySelectorAll: () => [],
  activeElement: null,
  createElement: () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} } }),
};

// --- fake server (deep-copied, like a real round trip) ----------------
const clone = o => JSON.parse(JSON.stringify(o));
let serverCfg = {
  brokers: { b1: { host: '127.0.0.1', port: 1883 } },
  routes: [{ name: '方案一', from: { broker: 'b1', topics: ['kingscada'] },
             targets: [{ broker: 'b1', topic: 'MES', rules: ['规则A', '规则B'] }] }],
  rule_library: {
    规则A: { match: '@PNs', each: '@Objs', template: { DataName: '@#value.N' } },
    规则B: { match: '@/tag1', each: '@.', template: { DataName: '@#key' } },
  },
};
const posts = [];
const fetch = async (url, opt) => {
  if (opt && opt.method === 'POST') {
    const body = JSON.parse(opt.body);
    posts.push(body);
    serverCfg = clone(body.config);
    return { json: async () => ({ ok: true }) };
  }
  return { json: async () => ({ config: clone(serverCfg), brokers: clone(serverCfg.brokers), routes: clone(serverCfg.routes) }) };
};

const window = { confirm: () => true, open() {}, scrollTo() {} };
const EventSource = function () { this.onmessage = null };
const sandbox = new Function(
  'document', 'window', 'fetch', 'EventSource', 'console', 'setTimeout', 'clearTimeout',
  src + '; return {EDIT, saveRule, newRule, editRule, delRule, addField, renderFields,' +
  '       toggleRule, dupRule, renderRuleLib, rlOpen,' +
  '       fillRulePicks, pickedRules, pickAllRules, clearRules, ruleInfo,' +
  '       mergeSamples, updateSample, splitHostPort, togglePwd, setAlive,' +
  '       loadCfg, persistSoon, flushPending, setRlFields:(f)=>{rlFields=f},' +
  '       getRlFields:()=>rlFields};'
)(document, window, fetch, EventSource, console, setTimeout, clearTimeout);

let ok = 0, fail = 0;
function check(label, got, want) {
  const pass = JSON.stringify(got) === JSON.stringify(want);
  if (pass) ok++; else fail++;
  console.log((pass ? '  PASS  ' : '  FAIL  ') + label);
  if (!pass) console.log('          got  ' + JSON.stringify(got) + '\n          want ' + JSON.stringify(want));
}

/** 往渲染出来的输入框里敲字，触发 oninput —— 和真人操作等价 */
function type(idx, left, right) {
  const box = store['rl-fields'];
  const a = box._inputs.find(x => x.kind === 'fk' && x.idx === idx);
  const b = box._inputs.find(x => x.kind === 'fv' && x.idx === idx);
  if (!a || !b) throw new Error('第 ' + idx + ' 行的输入框没渲染出来');
  a.value = left;  if (a.oninput) a.oninput();
  b.value = right; if (b.oninput) b.oninput();
}

(async () => {
  console.log('== 你的复现路径：加字段 → 左右填写 → 保存 → 换个规则 → 点回来 ==');
  await sandbox.loadCfg();
  sandbox.editRule('规则A');
  sandbox.addField();                       // 点「添加字段」
  const n = sandbox.getRlFields().length - 1;
  type(n, '转发属性', '"1"');               // 左边写 转发属性，右边写 "1"
  check('敲进去的字段名进了 rlFields', sandbox.getRlFields()[n].k, '转发属性');
  check('敲进去的字段值进了 rlFields', sandbox.getRlFields()[n].v, '"1"');

  await sandbox.saveRule();                 // 点「保存规则」
  check('保存后模板里有这个字段',
    sandbox.EDIT.rule_library['规则A'].template['转发属性'], '"1"');
  check('服务端也收到了', serverCfg.rule_library['规则A'].template['转发属性'], '"1"');

  sandbox.editRule('规则B');                // 点另一个规则名称
  sandbox.editRule('规则A');                // 再点回来
  const back = sandbox.getRlFields().find(f => f.k === '转发属性');
  check('点回来字段还在', !!back, true);
  check('值也还在', back && back.v, '"1"');

  console.log('\n== 多行字段逐行都能敲 ==');
  sandbox.newRule();
  sandbox.setRlFields([]);     // 从空白开始，不看 newRule 的默认模板
  sandbox.addField();
  sandbox.addField();
  sandbox.addField();
  type(0, 'DataName', '@#value.N');
  type(1, 'DataValue', '@#value.1|PVs.1');
  type(2, 'DataTime', '@#ts');
  check('3 行的键都进来了', sandbox.getRlFields().map(f => f.k),
    ['DataName', 'DataValue', 'DataTime']);
  check('3 行的值都进来了', sandbox.getRlFields().map(f => f.v),
    ['@#value.N', '@#value.1|PVs.1', '@#ts']);

  console.log('\n== 含引号/尖括号的值不会被转义搞坏 ==');
  sandbox.editRule('规则A');
  const last = sandbox.getRlFields().length - 1;
  type(last, '带引号', '"a<b>&c"');
  check('特殊字符原样保留', sandbox.getRlFields()[last].v, '"a<b>&c"');

  console.log('\n== 保存规则后切页不丢（时序竞态） ==');
  await sandbox.saveRule();
  await sandbox.loadCfg();
  check('切页后改动仍在', !!sandbox.EDIT.rule_library['规则A'].template['带引号'], true);
  check('服务端收到完整模板', !!serverCfg.rule_library['规则A'].template['带引号'], true);

  console.log('\n== 保存当场落盘，不是排队 ==');
  const p1 = posts.length;
  await sandbox.saveRule();
  check('saveRule 立刻 POST', posts.length, p1 + 1);

  console.log('\n== 轻量改动排队后，切页前会先冲刷 ==');
  sandbox.persistSoon();
  sandbox.EDIT.rule_library['新增的'] = { match: '@.', template: { p: '@.' } };
  await sandbox.loadCfg();
  check('排队的改动被冲刷落盘', !!serverCfg.rule_library['新增的'], true);
  check('没被服务端旧数据盖掉', !!sandbox.EDIT.rule_library['新增的'], true);

  console.log('\n== 冲刷幂等 ==');
  const p2 = posts.length;
  await sandbox.flushPending();
  await sandbox.flushPending();
  check('无排队时不发 POST', posts.length, p2);

  console.log('\n== 删除规则当场落盘 ==');
  const p3 = posts.length;
  await sandbox.delRule('新增的');
  check('删除立即写盘', posts.length, p3 + 1);
  check('服务端已无此规则', serverCfg.rule_library['新增的'], undefined);

  console.log('\n== 新建规则默认模板 ==');
  sandbox.newRule();
  const dfl = sandbox.getRlFields();
  check('默认时间戳是 @#ts', (dfl.find(f => /time/i.test(f.k)) || {}).v, '@#ts');
  check('默认含质量戳', dfl.some(f => /quality/i.test(f.k)), true);

  console.log('\n== 规则行：展开看内容 / 收起 ==');
  await sandbox.loadCfg();
  sandbox.rlOpen.clear();
  sandbox.renderRuleLib();
  const closed = store['rule-rows'].innerHTML;
  check('未展开时不渲染内容面板', closed.indexOf('rl-body'), -1);
  check('收起状态标 ▸', closed.indexOf('▸') >= 0, true);

  sandbox.toggleRule('规则A');
  let opened = store['rule-rows'].innerHTML;
  check('展开后渲染内容面板', opened.indexOf('rl-body') >= 0, true);
  check('内容面板有收起按钮', opened.indexOf('收起') >= 0, true);
  check('展开状态标 ▾', opened.indexOf('▾') >= 0, true);
  check('内容里有匹配条件', opened.indexOf('@PNs') >= 0, true);
  check('内容里有输出字段', opened.indexOf('DataName') >= 0, true);
  check('同时可展开多条', (sandbox.toggleRule('规则B'), sandbox.rlOpen.size), 2);

  sandbox.toggleRule('规则A');
  opened = store['rule-rows'].innerHTML;
  check('再点一下就收起', sandbox.rlOpen.has('规则A'), false);
  check('收起后内容面板消失', opened.indexOf('规则A') < 0 ||
        store['rule-rows'].innerHTML.split('rl-body').length - 1, 1);

  console.log('\n== 规则行：复制 ==');
  const n0 = Object.keys(sandbox.EDIT.rule_library).length;
  sandbox.dupRule('规则B');
  const lib = sandbox.EDIT.rule_library;
  check('多出一条', Object.keys(lib).length, n0 + 1);
  check('命名带副本', !!lib['规则B-副本'], true);
  check('内容是深拷贝', JSON.stringify(lib['规则B-副本']), JSON.stringify(lib['规则B']));
  lib['规则B-副本'].template.zz = 'x';
  check('改副本不影响原件', lib['规则B'].template.zz, undefined);
  sandbox.dupRule('规则B');
  check('重名时序号递增', !!lib['规则B-副本2'], true);

  console.log('\n== 导航与改名 ==');
  const html = require('fs').readFileSync(path, 'utf8');
  const nav = (html.match(/<nav>([\s\S]*?)<\/nav>/) || [, ''])[1];
  const labels = [...nav.matchAll(/>([^<]+)<\/button>/g)].map(m => m[1].trim());
  check('导航顺序', labels,
    ['实时数据', '配置broker', '规则配置', '方案配置', '模拟测试', '审计追踪', '系统设置']);
  check('已无「规则库」', html.indexOf('规则库'), -1);
  check('已无「规则试算」', html.indexOf('规则试算'), -1);
  check('模拟测试有两个子页',
    html.indexOf('离线转换测试') >= 0 && html.indexOf('在线转换测试') >= 0, true);
  check('有在线测试按钮', html.indexOf('runLiveTest') >= 0, true);

  console.log('\n== 系统设置 / 掉线小人 ==');
  check('系统设置页存在', html.indexOf('id="s-sys"') >= 0, true);
  check('状态检测按钮', html.indexOf('sysCheck()') >= 0, true);
  check('自动修复按钮', html.indexOf('sysHeal()') >= 0, true);
  check('启动转发', html.indexOf("sysEngine('start')") >= 0, true);
  check('停止转发', html.indexOf("sysEngine('stop')") >= 0, true);
  check('小人容器', html.indexOf('id="mood"') >= 0, true);
  check('抓狂动画', html.indexOf('@keyframes flail') >= 0, true);
  // 服务活着时小人不出现；掉了才出来喊「掉线了」
  sandbox.setAlive(true);
  check('正常时不显示小人', store['mood'].classList.contains('show'), false);
  check('正常时标题不是灰色', store['app-title'].classList.contains('dead'), false);
  sandbox.setAlive(false);
  check('掉线时小人出现', store['mood'].innerHTML.indexOf('掉线了') >= 0, true);
  check('掉线时小人带 show', store['mood'].classList.contains('show'), true);
  check('掉线时标题变灰', store['app-title'].classList.contains('dead'), true);

  console.log('\n== 规则选择器：勾了就要认（不许再说"没勾"） ==');
  await sandbox.loadCfg();
  sandbox.fillRulePicks();
  const box = store['t-rules'];
  const boxes = box._checks;
  check('渲染出复选框', boxes.length > 0, true);
  check('默认勾了第一条', boxes[0].checked, true);

  // 调用方写的是 pickedRules('t-rules')（不带 #）——必须能取到
  check('裸 id 能取到已勾的', sandbox.pickedRules('t-rules').length, 1);

  boxes[1].checked = true;                       // 再勾第二条
  check('勾两条取到两条', sandbox.pickedRules('t-rules').length, 2);
  check('取到的是规则名', sandbox.pickedRules('t-rules')[1], boxes[1].value);

  sandbox.clearRules('t-rules');                 // 走裸 id 的清空
  check('清空后为空', sandbox.pickedRules('t-rules').length, 0);
  sandbox.pickAllRules('t-rules');               // 走裸 id 的全选
  check('全选后全中', sandbox.pickedRules('t-rules').length, boxes.length);

  check('在线索规则是只读的', (store['lt-rules'].innerHTML || '').indexOf('class="ro"') >= 0, true);

  console.log('\n== 示例报文：选规则就拼进输入框 ==');
  serverCfg.rule_library['规则A'].sample = { PNs: { 1: 'V' }, Objs: [{ N: 'pub10' }] };
  serverCfg.rule_library['规则B'].sample = { tag1: 0, tag2: 1 };
  await sandbox.loadCfg();
  sandbox.fillRulePicks();
  const tIn = store['t-in'];
  let filled = JSON.parse(tIn.value);
  check('默认勾的那条已填入', Object.keys(filled).length > 0, true);
  check('填的是规则A 的示例', filled.PNs && filled.Objs ? true : false, true);

  sandbox.clearRules('t-rules');
  const before = tIn.value;
  sandbox.pickAllRules('t-rules');            // 两条都勾 -> 拼接
  filled = JSON.parse(tIn.value);
  check('多规则拼成一个 JSON',
        Object.keys(filled).sort(), ['Objs', 'PNs', 'tag1', 'tag2']);
  check('两条示例都在', filled.tag1 === 0 && filled.Objs[0].N === 'pub10', true);

  console.log('\n== 拼接规则：对象深合并，同名键后者覆盖，数组整体替换 ==');
  const merged = sandbox.mergeSamples([
    { a: { x: 1, y: 2 }, arr: [1, 2], keep: 'k' },
    { a: { y: 9, z: 3 }, arr: [3] },
  ]);
  check('对象递归合并', merged.a, { x: 1, y: 9, z: 3 });
  check('数组整体替换不拼接', merged.arr, [3]);
  check('只在前一个里有的键保留', merged.keep, 'k');
  check('空数组安全', sandbox.mergeSamples([{}, { p: 1 }]), { p: 1 });

  console.log('\n== 没换规则时不冲掉手写内容 ==');
  tIn.value = '{"我自己写的": 1}';
  sandbox.updateSample();                      // 选的没变
  check('输入框没被动', tIn.value, '{"我自己写的": 1}');

  console.log('\n== 示例报文存取 ==');
  sandbox.editRule('规则A');
  check('编辑时回填示例',
        JSON.parse(store['rl-sample'].value).PNs['1'], 'V');
  store['rl-sample'].value = '不是JSON';
  const beforeSave = Object.keys(sandbox.EDIT.rule_library).length;
  await sandbox.saveRule();
  check('示例不是 JSON -> 不给保存',
        Object.keys(sandbox.EDIT.rule_library).length, beforeSave);
  store['rl-sample'].value = '{"hello": 1}';
  await sandbox.saveRule();
  check('存进去了', sandbox.EDIT.rule_library['规则A'].sample, { hello: 1 });

  console.log('\n== IP 栏填了 IP:端口 要自动拆开 ==');
  const bh = store['b-host'], bp = store['b-port'];
  bh.value = '10.13.33.242:1883'; bp.value = '';
  sandbox.splitHostPort();
  check('IP 拆干净', bh.value, '10.13.33.242');
  check('端口填进端号栏', bp.value, '1883');

  bh.value = '10.13.33.242:1883'; bp.value = '8883';   // 端口栏已有值就别覆盖
  sandbox.splitHostPort();
  check('端口栏有值时不覆盖', bp.value, '8883');

  bh.value = '127.0.0.1'; bp.value = '1883';            // 没端口就别动
  sandbox.splitHostPort();
  check('纯 IP 不动', bh.value, '127.0.0.1');

  bh.value = 'broker.emqx.io';                          // 域名没端口
  sandbox.splitHostPort();
  check('域名不动', bh.value, 'broker.emqx.io');

  bh.value = '10.13.33.242:abc';                        // 端口不是数字
  sandbox.splitHostPort();
  check('端口不是数字就不拆', bh.value, '10.13.33.242:abc');

  console.log('\n== 密码小眼睛 ==');
  const bpw = store['b-pass'];
  bpw.type = 'password';
  sandbox.togglePwd();                 // 先调，让桩把 #b-eye 建出来
  const beye = store['b-eye'];
  check('点一下变明文', bpw.type, 'text');
  check('图标变隐藏', beye.textContent, '🙈');
  check('提示变「隐藏密码」', beye.title, '隐藏密码');
  sandbox.togglePwd();
  check('再点一下变回密文', bpw.type, 'password');
  check('图标变显示', beye.textContent, '👁');
  check('提示变「显示密码」', beye.title, '显示密码');

  console.log('\n' + ok + ' 项,失败 ' + fail);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('  !! 抛错: ' + (e && e.stack || e)); process.exit(1); });
