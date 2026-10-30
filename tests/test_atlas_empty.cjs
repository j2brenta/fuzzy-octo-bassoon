const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('empty Atlas shows the next step without initializing map controls',()=>{
  const elements=Object.fromEntries(['map-data','atlas-content','atlas-empty','return-to-reading','.header-note','.nav-more'].map(id=>[id,{hidden:id==='atlas-empty'}]));
  elements['map-data'].textContent=JSON.stringify({items:[]});
  vm.runInNewContext(fs.readFileSync('refresher/assets/map/app.js','utf8'),{
    document:{getElementById:id=>elements[id],querySelector:s=>elements[s]},
    localStorage:{getItem:()=>null},location:{search:'',origin:'http://localhost'},URLSearchParams
  });
  assert.equal(elements['atlas-content'].hidden,true);
  assert.equal(elements['atlas-empty'].hidden,false);
  assert.equal(elements['.header-note'].hidden,true);
  assert.equal(elements['.nav-more'].hidden,false);  // More holds the Library, which works without a graph
});
