'use strict';
const $ = id => document.getElementById(id);
const draftKey='refresher-draft:'+(document.body.dataset.workspace||'preview');
const capturedKey='refresher-captured:'+(document.body.dataset.workspace||'preview');
let mode = 'text', jobs = [], selectedFile = null, openedId = null, detailVersion = '', loading = false, renderedJobs = '';
const icons = {text:'TXT',link:'URL',file:'FILE'};
const statusLabels = {queued:'Queued',processing:'Processing',pending:'Saved · needs attention',complete:'Processing finished'};
function node(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el; }
async function api(path, options) {if(options?.method==='POST')options.headers={...options.headers,'X-Refresher':'1'};const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw new Error(result.error||'The request could not be completed.');return result;}
function error(message){$('form-error').textContent=message;$('form-error').hidden=false;$('form-error').focus();}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,5000);}
function setMode(next){mode=next;document.querySelectorAll('[data-mode]').forEach(button=>{const active=button.dataset.mode===mode;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));});$('text-panel').hidden=mode==='file';$('file-panel').hidden=mode!=='file';$('example').disabled=mode==='file';$('content-label').textContent=mode==='link'?'Public article URL':'What would you like to remember?';$('content').placeholder=mode==='link'?'https://example.com/an-article-worth-keeping':'Paste an article, a newsletter, a conversation…\nOr simply write down an idea.';$('content-hint').textContent=mode==='link'?'Public HTML articles are supported. For restricted sites, paste the text instead.':'Your original is kept exactly as submitted.';$('form-error').hidden=true;}
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>setMode(button.dataset.mode)));
$('content').addEventListener('input',()=>$('char-count').textContent=`${$('content').value.length.toLocaleString()} / 60,000`);
$('example').addEventListener('click',()=>{setMode('text');$('title').value='Sample · Why provenance matters';$('content').value='Knowledge notes are more useful when they preserve the source behind each claim.\nA citation should point to the exact passage that supports a statement.\nReviewing whether a claim matches its source is different from checking whether the source is factually correct.\nKeeping original content makes later corrections easier to audit.';$('content').dispatchEvent(new Event('input'));$('content').focus();});
function chooseFile(file){if(!file)return;selectedFile=null;$('file-name').textContent='Choose a file or drop it here';if(file.size>20*1024*1024){error('Choose a file no larger than 20 MB.');return;}if(!file.size){error('This file is empty. Choose a file with content.');return;}selectedFile=file;$('file-name').textContent=`${file.name} · ${(file.size/1024).toLocaleString(undefined,{maximumFractionDigits:0})} KB`;$('form-error').hidden=true;}
$('file').addEventListener('change',()=>chooseFile($('file').files[0]));
for(const event of ['dragenter','dragover'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('drag');});
for(const event of ['dragleave','drop'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('drag');if(event==='drop')chooseFile(e.dataTransfer.files[0]);});
function readFile(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Could not read the selected file.'));reader.readAsDataURL(file);});}
$('capture-form').addEventListener('submit',async event=>{event.preventDefault();if(loading)return;$('form-error').hidden=true;if(mode==='file'&&!selectedFile)return error('Choose a file first.');if(mode!=='file'&&!$('content').value.trim())return error(mode==='link'?'Enter an article URL first.':'Add some text first.');loading=true;$('submit').disabled=true;$('submit').textContent='Saving original…';document.querySelectorAll('[data-mode]').forEach(b=>b.disabled=true);try{const data={kind:mode,title:$('title').value,text:mode==='file'?'':$('content').value};if(mode==='file'){data.filename=selectedFile.name;data.content=await readFile(selectedFile);}const job=await api('/api/submissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});jobs.unshift(job);renderList();$('content').value='';$('title').value='';$('content').dispatchEvent(new Event('input'));selectedFile=null;$('file').value='';$('file-name').textContent='Choose a file or drop it here';$('receipt-notice').hidden=false;$('receipt-notice').replaceChildren(node('strong','Original saved. '),document.createTextNode('You can leave while analysis continues. '));const receipt=node('a','Open saved receipt');receipt.href='/capture/?receipt='+job.id;$('receipt-notice').append(receipt);toast('Original saved.');try{sessionStorage.removeItem(draftKey);sessionStorage.setItem(capturedKey,'1');}catch{}document.body.classList.add('capture-repeat');await openDetail(job.id);}catch(e){error(e.message);}finally{loading=false;$('submit').disabled=false;$('submit').replaceChildren(document.createTextNode('Save source '),node('span','↗'));document.querySelectorAll('[data-mode]').forEach(b=>b.disabled=false);}});
function renderList(){renderedJobs=JSON.stringify(jobs);const query=$('search').value.trim().toLowerCase();const filtered=jobs.filter(job=>job.title.toLowerCase().includes(query));$('source-count').textContent=jobs.length;$('source-list').replaceChildren();$('empty').hidden=filtered.length>0;$('empty').querySelector('h3').textContent=jobs.length?'No matching sources.':'A fresh start for your knowledge.';$('empty').querySelector('p').textContent=jobs.length?'Try another title or clear your search.':'Add your first source above. Its claims and evidence will appear here.';for(const job of filtered){const button=node('button',undefined,'source-row');button.type='button';const main=node('span',undefined,'source-main');main.append(node('strong',job.title),node('small',`${job.kind==='file'?job.attachment?.name||'File':job.kind==='link'?'Web article':'Text note'} · ${new Date(job.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric'})} · ${job.stage}`));button.append(node('span',icons[job.kind]||'TXT','source-icon'),main,node('span',statusLabels[job.status]||job.status,`status ${job.status}`),node('span','↗','row-arrow'));button.addEventListener('click',()=>openDetail(job.id));$('source-list').append(button);}}
$('search').addEventListener('input',renderList);
function safeURL(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null;}catch{return null;}}
function retryAnalysisButton(job){const retry=node('button','Retry analysis','primary');retry.type='button';retry.addEventListener('click',async()=>{retry.disabled=true;retry.textContent='Queuing…';try{await api(`/api/submissions/${job.id}/retry`,{method:'POST',headers:{'Content-Type':'application/json'}});await openDetail(job.id);}catch(e){toast(e.message);retry.disabled=false;retry.textContent='Retry analysis';}});return retry;}
function renderUnanalyzed(job){
  let title=job.title||'Saved source';try{title=new URL(title).hostname.replace(/^www\./,'');}catch{}
  $('detail-title').textContent=title;
  const body=$('detail-body'), pending=job.status==='pending';
  body.replaceChildren(node('span',pending?'Original saved':job.stage,`status ${job.status}`));
  body.append(node('p',job.recovery==='setup'?'Your source is saved. Analysis was unavailable when it was submitted. You can retry now.':job.message||'Your original is saved. Analysis is underway.','detail-message'));
  const actions=node('div',undefined,'journey-actions');
  if(pending&&!['format','paste'].includes(job.recovery))actions.append(retryAnalysisButton(job));
  if(['format','paste'].includes(job.recovery)){const paste=node('button','Paste text instead','primary');paste.type='button';paste.addEventListener('click',()=>{$('detail').close();setMode('text');$('content').focus();});actions.append(paste);}
  const result=(job.results||[]).find(r=>r.processed?.claims?.length)||job.results?.[0];
  if(result?.id){const read=node('a','Read saved original');read.href='/library/?source='+encodeURIComponent(result.id);actions.append(read);}
  else if(job.attachment){const download=node('a','Download original');download.href='/api/uploads/'+job.attachment.sha256;download.download=job.attachment.name;actions.append(download);}
  body.append(actions);
  const secondary=node('details',undefined,'receipt-utilities');
  secondary.append(node('summary','More options'));
  const permalink=node('a','Link to this receipt');permalink.href='/capture/?receipt='+encodeURIComponent(job.id);
  secondary.append(permalink);body.append(secondary);
}
function renderProgressReceipt(job){
  let title=job.title||'Saved source';
  try { title=new URL(title).hostname.replace(/^www\./,''); } catch { /* Keep an actual source title. */ }
  $('detail-title').textContent=title;
  const body=$('detail-body'), complete=job.status==='complete';
  const hasClaims=(job.results||[]).some(r=>r.processed?.claims?.length);
  const heading=node('h3',complete?(hasClaims?'Ready to explore':'Source saved'):'Analyzing your source…','receipt-progress-title');
  if(!complete){const progress=node('span',undefined,'receipt-progress');progress.setAttribute('role','progressbar');progress.setAttribute('aria-label','Analyzing source');body.replaceChildren(progress,heading);}
  else body.replaceChildren(heading);
  body.append(node('p',complete?'Your original and available results are saved.':'Your original is saved. You can leave this page—we’ll keep working.','detail-message'));
  const actions=node('div',undefined,'journey-actions');
  const result=(job.results||[]).find(r=>r.processed?.claims?.length)||(job.results||[]).find(r=>r.url)||job.results?.[0];
  const open=node('a',complete?'Open source':'Back to Library','journey-primary');
  open.href=complete&&result?.id?'/library/?source='+encodeURIComponent(result.id):'/library/';actions.append(open);body.append(actions);
  const utilities=node('details',undefined,'receipt-utilities');utilities.append(node('summary','More options'));
  const receipt=node('a','Link to this receipt');receipt.href='/capture/?receipt='+encodeURIComponent(job.id);utilities.append(receipt);body.append(utilities);
}
function renderDetail(job){
  $('detail').classList.toggle('receipt-simple',true);
  if(['queued','processing','complete'].includes(job.status))renderProgressReceipt(job);
  else renderUnanalyzed(job);
}
async function openDetail(id,push=true){if(push){const u=new URL(location.href);u.searchParams.set('receipt',id);history.pushState({receipt:id},'',u);}openedId=id;detailVersion='';try{const job=await api(`/api/submissions/${id}`);if(openedId!==id)return;renderDetail(job);detailVersion=JSON.stringify(job);if(!$('detail').open)$('detail').showModal();}catch(e){toast(e.message);}}
$('close-detail').addEventListener('click',()=>$('detail').close());$('detail').addEventListener('close',()=>{openedId=null;const u=new URL(location.href);u.searchParams.delete('receipt');history.replaceState({},'',u);});
async function refresh(){try{const next=await api('/api/submissions');const changed=JSON.stringify(next)!==renderedJobs;jobs=next;$('connection-error').hidden=true;if(changed&&!$('source-list').contains(document.activeElement))renderList();if(openedId){const job=await api(`/api/submissions/${openedId}`);const version=JSON.stringify(job);if(version!==detailVersion){const focused=document.activeElement;if(!$('detail-body').contains(focused)){renderDetail(job);detailVersion=version;}}}}catch{$('connection-error').textContent='Cannot reach the workspace. Check that the server is running; your saved sources remain on disk.';$('connection-error').hidden=false;}finally{setTimeout(refresh,2500);}}
const receiptId=new URLSearchParams(location.search).get('receipt');
if(receiptId&&/^[a-f0-9]{32}$/.test(receiptId))openDetail(receiptId,false);
addEventListener('popstate',()=>{const id=new URLSearchParams(location.search).get('receipt');if(id&&/^[a-f0-9]{32}$/.test(id))openDetail(id,false);else if($('detail').open)$('detail').close();});
try{
  if(sessionStorage.getItem(capturedKey))document.body.classList.add('capture-repeat');
  const draft=JSON.parse(sessionStorage.getItem(draftKey)||'null');
  if(draft){$('content').value=draft.text||'';$('title').value=draft.title||'';setMode(draft.mode==='link'?'link':'text');$('content').dispatchEvent(new Event('input'));}
}catch{}
function saveDraft(){try{sessionStorage.setItem(draftKey,JSON.stringify({text:$('content').value,title:$('title').value,mode}));}catch{}}
$('content').addEventListener('input',saveDraft);$('title').addEventListener('input',saveDraft);
refresh();
