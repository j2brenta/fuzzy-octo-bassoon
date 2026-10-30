const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
// The Atlas script's parts, in the order index.html loads them (graphview.ATLAS_SCRIPTS).
const SCRIPTS=['app.js','proposals.js','graph.js','slice.js','evidence.js','select.js','claim.js','compare.js','main.js'];
test('empty Atlas shows the next step without initializing map controls',()=>{
  const elements=Object.fromEntries(['map-data','atlas-content','atlas-empty','return-to-reading','.header-note','.nav-more'].map(id=>[id,{hidden:id==='atlas-empty'}]));
  elements['map-data'].textContent=JSON.stringify({items:[]});
  const context=vm.createContext({
    document:{getElementById:id=>elements[id],querySelector:s=>elements[s]},
    localStorage:{getItem:()=>null},location:{search:'',origin:'http://localhost'},URLSearchParams
  });
  context.window=context;
  for(const name of SCRIPTS)vm.runInContext(fs.readFileSync('refresher/assets/map/'+name,'utf8'),context);  // the rest stop at once
  assert.equal(context.RefresherAtlas,undefined);
  assert.equal(elements['atlas-content'].hidden,true);
  assert.equal(elements['atlas-empty'].hidden,false);
  assert.equal(elements['.header-note'].hidden,true);
  assert.equal(elements['.nav-more'].hidden,false);  // More holds the Library, which works without a graph
});
