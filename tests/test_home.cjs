const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script = fs.readFileSync('refresher/static/topics.js','utf8');
class Element {
  constructor(){this.hidden=true;this.childNodes=[];this.textContent='';this.style={};this.queries={};this.classList={toggle(){}};}
  append(...nodes){this.childNodes.push(...nodes);}
  replaceChildren(...nodes){this.childNodes=[...nodes];}
  before(){}
  setAttribute(name,value){this[name]=value;}
  querySelector(selector){return this.queries[selector] ||= new Element();}
}
async function home(topics, sources, fail=false, resume=null) {
  const nodes={'home-welcome':new Element()}, body=new Element(); let pending;
  const library=new Promise(resolve=>{pending=resolve;});
  const context={URLSearchParams,Date,Promise,window:{RefresherJourney:resume?{data:{resume},safe:href=>href}:undefined},localStorage:{getItem:()=>null,setItem(){}},document:{body,getElementById:id=>nodes[id] ||= new Element(),createElement:()=>new Element()},fetch:async path=>({ok:!fail,json:async()=>path==='/api/library'?library:path==='/api/topics'?topics:[]})};
  vm.runInNewContext(script,context);
  await new Promise(setImmediate);
  // Topics may arrive before the source index. Do not decide that an empty topic list means an empty vault.
  assert.equal(nodes['home-welcome'].hidden,true);
  pending(sources); await new Promise(setImmediate);
  return nodes;
}
test('an empty vault gets the welcome landing and no duplicate dashboard or empty card',async()=>{
  const n=await home([],[]);
  assert.equal(n['home-welcome'].hidden,false);
  assert.equal(n['home-dashboard'].hidden,true);
  assert.equal(n.empty.hidden,true);
});
test('saved sources without topics get their Library next step instead of onboarding',async()=>{
  const n=await home([],[{status:'pending'}]);
  assert.equal(n['home-welcome'].hidden,true);
  assert.equal(n['home-dashboard'].hidden,false);
  assert.equal(n['subject-search'].hidden,true);
  assert.equal(n.empty.querySelector('a').href,'/library/');
  assert.match(n.empty.querySelector('.quiet').textContent,/read your originals/);
});
test('a populated knowledge index keeps subject browsing even if there are no raw library rows',async()=>{
  const row={id:'topic:1',kind:'topic',title:'Entropy',top:'An idea',in_topics:[],latest:'2026-10-29T12:00:00Z',counts:{ideas:1,items:1,core:1}};
  const n=await home([row],[]);
  assert.equal(n['home-welcome'].hidden,true);
  assert.equal(n['home-dashboard'].hidden,false);
  assert.equal(n['subject-search'].hidden,false);
  assert.equal(n['topics-shelf'].hidden,false);
});
test('connection errors do not pretend the collection is empty',async()=>{
  const n=await home([],[],true);
  assert.equal(n['home-welcome'].hidden,true);
  assert.equal(n.error.hidden,false);
  assert.match(n.error.textContent,/reconnect/);
});

test('resume resolves an old URL receipt to the saved article title',async()=>{
  const n=await home([],[{id:'article',title:'Privacy Evaluations for AI Systems',url:'https://example.com/article',status:'analyzed'}],false,
    {title:'https://example.com/article#section',href:'/library/?source=url-receipt'});
  assert.equal(n['resume-title'].textContent,'Privacy Evaluations for AI Systems');
  assert.equal(n['resume-link'].href,'/library/?source=url-receipt');
});
