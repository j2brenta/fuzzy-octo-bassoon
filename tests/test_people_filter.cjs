// Run with: node --test tests/test_people_filter.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('refresher/assets/map/app.js','utf8');
const implementation=source.slice(source.indexOf('function rankedPeople('),source.indexOf('  function peopleSummary'));
function rank(subject='') {
  const people=new Map([
    ['mention',{statements:[],items:[],about_in:[]}],
    ['writer',{statements:[],items:['one'],about_in:[]}],
    ['speaker',{statements:['said'],items:[],about_in:[]}],
    ['pending',{statements:['pending-claim'],items:[],about_in:[]}],
    ['hidden',{statements:['elsewhere'],items:[],about_in:[]}],
    ['about',{statements:[],facts:['profile-fact'],items:[],about_in:['one']}],
    ['hidden-about',{statements:[],facts:['hidden-fact'],items:[],about_in:['two']}],
  ]);
  const rows=[...people.keys()].map(person_id=>({person_id,claim_ids:person_id==='speaker'?['said']:[]}));
  return vm.runInNewContext('('+implementation+')',{
    data:{people_scores:{'':rows,subject:rows,other:rows}},people,scoreSubject:()=>subject,
    items:new Map([['one',{topics:['subject'],stories:[]}],['two',{topics:[],stories:[]}]]),stories:new Map(),
    claims:new Map([['said',{item:'one'}],['pending-claim',{item:'one'}],['elsewhere',{item:'two'}],['profile-fact',{item:'one'}],['hidden-fact',{item:'two'}]])
  })([{id:'one',mentions:[{id:'mention'},{id:'hidden'}]}]).map(r=>r.person_id);
}
test('People excludes incidental mentions, writers without statements and speakers outside the visible slice',()=>{
  assert.equal(JSON.stringify(rank()),JSON.stringify(['speaker','pending','about']));
});
test('People retains substantive profile facts from sources in the selected subject',()=>{
  assert.equal(JSON.stringify(rank('subject')),JSON.stringify(['speaker','about']));
});

test('Profile facts from unrelated subjects stay outside the selected view',()=>{
  assert(!rank('other').includes('about'));
});

test('Person labels describe the saved evidence without identity or confidence warnings',()=>{
  const code=source.slice(source.indexOf('function peopleSummary('),source.indexOf('  const scoreText'));
  const summary=vm.runInNewContext('('+code+')');
  assert.equal(summary({facts:['karpathy-fact'],statements:[]}), '1 sourced fact');
  assert.equal(summary({facts:[],statements:['a','b']}), '2 attributed statements');
  assert.equal(summary({facts:['f'],statements:['a']}), '1 attributed statement · 1 sourced fact');
});
test('Sparse People graphs fit at normal size while larger graphs can zoom out',()=>{
  const code=source.slice(source.indexOf('function fitGraph('),source.indexOf('  const topicTitle'));
  for(const [mode,initial,expected] of [['people',2.5,1],['people',.7,.7],['topics',2.5,2.5]]) {
    let zoom=initial,centered=false,fitted=false;
    const cy={fit(){fitted=true;},elements(){return [];},zoom(value){if(value!==undefined)zoom=value;return zoom;},center(){centered=true;}};
    vm.runInNewContext('('+code+')',{cy,state:{mode}})();
    assert(fitted);assert.equal(zoom,expected);assert.equal(centered,initial!==expected);
  }
});

test('People cards do not overlap with two people or a narrow viewport',()=>{
  const cytoscape=require('../refresher/assets/cytoscape.min.js');
  const code=source.slice(source.indexOf('function peopleLayout('),source.indexOf('  function renderTopicGraph'));
  const layout=vm.runInNewContext('('+code+')');
  for(const [count,width] of [[2,700],[5,320]]) {
    const cy=cytoscape({headless:true,styleEnabled:true,elements:Array.from({length:count},(_,i)=>({data:{id:String(i)}})),
      style:[{selector:'node',style:{width:180,height:64,padding:5}}]});
    cy.layout({...layout(count,width),boundingBox:{x1:0,y1:0,w:width,h:500}}).run();
    const nodes=cy.nodes();
    for(let i=0;i<count;i++)for(let j=i+1;j<count;j++){
      const a=nodes[i].position(),b=nodes[j].position();
      assert(Math.abs(a.x-b.x)>=190 || Math.abs(a.y-b.y)>=74, 'People cards must not overlap');
    }
    cy.destroy();
  }
});
