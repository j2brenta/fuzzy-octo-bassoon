const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
test('the shell renders one consistent footer, including when opened offline',()=>{
  const appended=[];
  const context={location:{protocol:'file:'},document:{querySelector:()=>appended[0]||null,getElementById:()=>null,createElement:tag=>({tag}),body:{append:node=>appended.push(node)}}};
  vm.createContext(context);
  const script=fs.readFileSync('refresher/static/nav.js','utf8');
  vm.runInContext(script,context); vm.runInContext(script,context);
  assert.equal(appended.length,1);
  assert.equal(appended[0].className,'app-footer');
  assert.match(appended[0].innerHTML,/Collected with purpose/);
  assert.match(appended[0].innerHTML,/Nebius × NVIDIA Global AI Hackathon/);
  assert.doesNotMatch(appended[0].innerHTML,/Built with/);
});
