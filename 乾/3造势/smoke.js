// Minimal DOM shim -> execute the page script and surface any runtime error.
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const src = html.match(/<script>([\s\S]*)<\/script>/)[1];

const store = {};
function el(id) {
  if (store[id]) return store[id];
  const e = {
    id, value: '', textContent: '', innerHTML: '', checked: false, disabled: false,
    style: {}, dataset: {}, className: '',
    classList: { add(){}, remove(){}, toggle(){} },
    focus(){}, appendChild(){},
    querySelectorAll: () => [], querySelector: () => null,
    _oc:null, _ch:null, _oi:null, _ob:null
  };
  Object.defineProperty(e,'onclick',{get(){return e._oc},set(v){e._oc=v}});
  Object.defineProperty(e,'onchange',{get(){return e._ch},set(v){e._ch=v}});
  Object.defineProperty(e,'oninput',{get(){return e._oi},set(v){e._oi=v}});
  Object.defineProperty(e,'onblur',{get(){return e._ob},set(v){e._ob=v}});
  store[id] = e;
  return e;
}

global.document = {
  querySelector: (s) => (s.startsWith('#') ? el(s.slice(1)) : null),
  querySelectorAll: () => [],
};
global.window = { confirm: () => true, scrollTo(){}, _r: null };
global.EventSource = class { constructor(){} set onmessage(v){} };
global.setInterval = () => 0;
global.setTimeout = (fn) => { try { fn(); } catch (e) { console.log('setTimeout cb error: ' + e.stack); } return 0 };
global.clearTimeout = () => {};
global.fetch = async () => ({ json: async () => ({}) });
global.alert = () => {};

const probe = `
;(function(){
  function step(name, fn){ try{ fn(); console.log('  OK   ' + name) }catch(e){ console.log('  FAIL ' + name + ' -> ' + e.message); throw e } }
  console.log('--- renderers with realistic data ---');
  EDIT.brokers = { "A":{host:"1.1.1.1",port:1883,role:"listen",username:"u"},
                   "B":{host:"2.2.2.2",port:1883,role:"target",username:"v"} };
  EDIT.rule_library = { "r1":{match:"@PNs",each:"@Objs",template:{"值":"@PVs.1"}} };
  EDIT.routes = [ { name:"方案一", enabled:true,
                    from:{broker:"A",topics:["kingscada"]},
                    targets:[{broker:"B",topic:"MES",rules:["r1"]}] } ];
  step('renderBrokerList', ()=>renderBrokerList());
  step('renderRoutes',     ()=>renderRoutes());
  step('renderRuleLib',    ()=>renderRuleLib());
  step('selectRoute',      ()=>selectRoute(0));
  step('openWizard',       ()=>openWizard(-1));
  step('wzRender',         ()=>wzRender());
  step('newRule',          ()=>newRule());
  step('editRule',         ()=>editRule('r1'));
  step('renderFields',     ()=>renderFields());
  step('fillRouteSelect',  ()=>fillRouteSelect());
  step('syncSelButtons',   ()=>syncSelButtons());
})();
`;

try {
  eval(src + probe);
  console.log('\nall green');
} catch (e) {
  console.log('\nERROR: ' + e.message);
  process.exit(2);
}
