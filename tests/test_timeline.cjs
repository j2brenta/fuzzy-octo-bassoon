const {test} = require('node:test');
const assert = require('node:assert/strict');
const {timelineSources} = require('../refresher/static/timeline.js');
const item = (id, time, overrides={}) => ({id,time,title:'Entropy paper',source:'A journal',author:'Ann',topics:['topic-1'],stories:['idea-1'],claims:['c1'],quality:'accepted',...overrides});
const data = {items:[item('a','2026-10-29T12:00:00+02:00'),item('b','2026-10-29T10:30:00Z'),item('c','2026-10-28T10:00:00Z',{repost_of:'a',quality:'pending'}),item('d','2026-10-27T10:00:00Z',{title:'Other subject',topics:[],stories:[],claims:['c2']})],claims:[{id:'c1',band:'fresh'},{id:'c2',band:'stale'}],clusters:[{id:'cluster-1',items:['b','c']}]};
test('chronology compares instants, with publication/capture timestamps left unchanged',()=>{
  const before=JSON.stringify(data);
  assert.deepEqual(timelineSources(data,new URLSearchParams()).map(x=>x.id),['b','a','c','d']);
  assert.equal(JSON.stringify(data),before);
});
test('map filters carried into the timeline preserve the same source slice',()=>{
  const q=new URLSearchParams({cluster:'cluster-1',topic:'topic-1',idea:'idea-1',quality:'accepted',freshness:'fresh',from:'2026-10-29',search:'journal Ann',hide_reposts:'1'});
  assert.deepEqual(timelineSources(data,q).map(x=>x.id),['b']);
  q.set('search','no match'); assert.equal(timelineSources(data,q).length,0);
});
test('unassigned topics, reposts and cleared filters keep their original meaning',()=>{
  assert.deepEqual(timelineSources(data,new URLSearchParams({topic:'unassigned'})).map(x=>x.id),['d']);
  assert.equal(timelineSources(data,new URLSearchParams({hide_reposts:'1'})).length,3);
  assert.equal(timelineSources(data,new URLSearchParams()).length,4);
});
