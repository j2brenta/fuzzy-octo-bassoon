const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('Home brain pauses, resumes without jumping, and stops offscreen or for reduced motion',()=>{
  const frames=new Map(), classes=new Set(), sparks=new Set(), attributes={}, handlers={};
  let id=0, intersection, visibility, preference;
  const panel={classList:{add:n=>classes.add(n),contains:n=>classes.has(n),toggle(n,force){const next=force??!classes.has(n);if(next)classes.add(n);else classes.delete(n);return next;}}};
  const text={textContent:''}, path={setAttribute(){}}, labels={};
  const button=name=>({disabled:false,closest:()=>panel,querySelector:s=>s==='span'?text:path,setAttribute:(k,v)=>labels[name+':'+k]=v,addEventListener:(event,fn)=>handlers[name+':'+event]=fn});
  const scene=button('scene'), pause=button('pause');
  const packet={dataset:{route:'route',target:'target',duration:'1000',offset:'950'},setAttribute:(k,v)=>attributes[k]=v};
  const route={getTotalLength:()=>100,getPointAtLength:x=>({x,y:0})};
  const motion={matches:false,addEventListener:(name,fn)=>preference=fn};
  const context={document:{hidden:false,getElementById:name=>({'brain-motion':scene,'brain-pause':pause,route,target:{classList:sparks}})[name],querySelector:()=>panel,querySelectorAll:s=>s==='.brain-packet'?[packet]:[],addEventListener:(name,fn)=>visibility=fn},matchMedia:()=>motion,IntersectionObserver:class{constructor(fn){intersection=fn;}observe(){}},requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:key=>frames.delete(key)};
  vm.runInNewContext(fs.readFileSync('refresher/static/brain.js','utf8'),context);
  assert.equal(frames.size,0,'hidden first-use hero should not animate');
  intersection([{isIntersecting:true}]);
  function tick(time){assert.equal(frames.size,1);const [key,fn]=frames.entries().next().value;frames.delete(key);fn(time);}
  tick(0);tick(100);assert.ok(sparks.has('is-sparking'));
  const before=attributes.transform;
  handlers['pause:click']();assert.equal(frames.size,0);assert.equal(text.textContent,'Resume animation');
  handlers['scene:click']();tick(100000);assert.equal(attributes.transform,before);
  intersection([{isIntersecting:false}]);assert.equal(frames.size,0);
  intersection([{isIntersecting:true}]);assert.equal(frames.size,1);
  context.document.hidden=true;visibility();assert.equal(frames.size,0);
  context.document.hidden=false;visibility();assert.equal(frames.size,1);
  motion.matches=true;preference();assert.equal(frames.size,0);assert.equal(pause.disabled,true);assert.match(text.textContent,/Reduced motion/);
});
