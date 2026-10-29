const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script = fs.readFileSync('refresher/static/journey.js', 'utf8');
function page(href, stored = {}, workspace = 'fixture') {
  const url = new URL(href), events = {}, calls = [];
  const context = {URL, JSON, Object, location: url, scrollY: 220, localStorage:{getItem:k=>stored[k],setItem:(k,v)=>stored[k]=v},document:{body:{dataset:{workspace}},getElementById:()=>null,addEventListener(){}},addEventListener:(k,f)=>events[k]=f,requestAnimationFrame:f=>f()};
  context.window = context; context.scrollTo = (x,y)=>calls.push(y); vm.runInNewContext(script,context);
  return {api:context.RefresherJourney,stored,events,calls};
}
test('resume preserves subject and source; leaving saves position without marking read',()=>{
  const p=page('http://localhost:8091/brief/?q=entropy&read=source-1');
  p.api.subject('Entropy'); p.api.reading('A paper'); p.events.pagehide();
  const q=page('http://localhost:8091/',p.stored);
  assert.equal(q.api.data.resume.href,'/brief/?q=entropy&read=source-1');
  assert.equal(q.api.data.recent[0].href,'/brief/?q=entropy');
  const r=page('http://localhost:8091/brief/?q=entropy&read=source-1',p.stored); r.api.reading('A paper');
  assert.deepEqual(r.calls,[220]);
});
test('return paths reject external origins and unrelated local routes',()=>{
  const {api}=page('http://localhost:8091/');
  assert.equal(api.safe('https://evil.example/brief/?q=x'),null);
  assert.equal(api.safe('javascript:alert(1)'),null);
  assert.equal(api.safe('/api/submissions'),null);
  assert.equal(api.safe('/topic/?id=cluster%3A1&read=a'),'/topic/?id=cluster%3A1&read=a');
});

test('a different vault does not inherit reading history on the same app origin',()=>{
  const p=page('http://localhost:8091/brief/?q=entropy&read=source-1'); p.api.reading('A paper');
  const q=page('http://localhost:8091/',p.stored,'another-vault');
  assert.equal(q.api.data.resume,null);
});
