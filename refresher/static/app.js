'use strict';
const $ = id => document.getElementById(id);
let mode = 'text', jobs = [], selectedFile = null, openedId = null, detailVersion = '', loading = false, renderedJobs = '';
const icons = {text:'TXT',link:'URL',file:'FILE'};
const statusLabels = {queued:'Queued',processing:'Processing',pending:'Needs attention',complete:'Processed'};
function node(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el; }
async function api(path, options) {if(options?.method==='POST')options.headers={...options.headers,'X-Refresher':'1'};const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw new Error(result.error||'The request could not be completed.');return result;}
function error(message){$('form-error').textContent=message;$('form-error').hidden=false;$('form-error').focus();}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,5000);}
function setMode(next){mode=next;document.querySelectorAll('[data-mode]').forEach(button=>{const active=button.dataset.mode===mode;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));});$('text-panel').hidden=mode==='file';$('file-panel').hidden=mode!=='file';$('example').disabled=mode==='file';$('content-label').textContent=mode==='link'?'Public article URL':'What would you like to remember?';$('content').placeholder=mode==='link'?'https://example.com/an-article-worth-keeping':'Paste an article, a newsletter, a conversation…\nOr simply write down an idea.';$('content').rows=mode==='link'?3:8;$('content-hint').textContent=mode==='link'?'Public HTML articles are supported. For restricted sites, paste the text instead.':'Your original is kept exactly as submitted.';$('form-error').hidden=true;}
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>setMode(button.dataset.mode)));
$('content').addEventListener('input',()=>$('char-count').textContent=`${$('content').value.length.toLocaleString()} / 60,000`);
$('example').addEventListener('click',()=>{setMode('text');$('title').value='Sample · Why provenance matters';$('content').value='Knowledge notes are more useful when they preserve the source behind each claim.\nA citation should point to the exact passage that supports a statement.\nReviewing whether a claim matches its source is different from checking whether the source is factually correct.\nKeeping original content makes later corrections easier to audit.';$('content').dispatchEvent(new Event('input'));$('content').focus();});
function chooseFile(file){if(!file)return;selectedFile=null;$('file-name').textContent='Choose a file or drop it here';if(file.size>20*1024*1024){error('Choose a file no larger than 20 MB.');return;}if(!file.size){error('This file is empty. Choose a file with content.');return;}selectedFile=file;$('file-name').textContent=`${file.name} · ${(file.size/1024).toLocaleString(undefined,{maximumFractionDigits:0})} KB`;$('form-error').hidden=true;}
$('file').addEventListener('change',()=>chooseFile($('file').files[0]));
for(const event of ['dragenter','dragover'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('drag');});
for(const event of ['dragleave','drop'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('drag');if(event==='drop')chooseFile(e.dataTransfer.files[0]);});
function readFile(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Could not read the selected file.'));reader.readAsDataURL(file);});}
$('capture-form').addEventListener('submit',async event=>{event.preventDefault();if(loading)return;$('form-error').hidden=true;if(mode==='file'&&!selectedFile)return error('Choose a file first.');if(mode!=='file'&&!$('content').value.trim())return error(mode==='link'?'Enter an article URL first.':'Add some text first.');loading=true;$('submit').disabled=true;$('submit').textContent='Saving original…';document.querySelectorAll('[data-mode]').forEach(b=>b.disabled=true);try{const data={kind:mode,title:$('title').value,text:mode==='file'?'':$('content').value};if(mode==='file'){data.filename=selectedFile.name;data.content=await readFile(selectedFile);}const job=await api('/api/submissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});jobs.unshift(job);renderList();$('content').value='';$('title').value='';$('content').dispatchEvent(new Event('input'));selectedFile=null;$('file').value='';$('file-name').textContent='Choose a file or drop it here';toast('Original saved. You can follow its progress below.');$('library').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}catch(e){error(e.message);}finally{loading=false;$('submit').disabled=false;$('submit').replaceChildren(document.createTextNode('Capture knowledge '),node('span','↗'));document.querySelectorAll('[data-mode]').forEach(b=>b.disabled=false);}});
function renderList(){renderedJobs=JSON.stringify(jobs);const query=$('search').value.trim().toLowerCase();const filtered=jobs.filter(job=>job.title.toLowerCase().includes(query));$('source-count').textContent=jobs.length;$('nav-count').textContent=jobs.length;$('source-list').replaceChildren();$('empty').hidden=filtered.length>0;$('empty').querySelector('h3').textContent=jobs.length?'No matching sources.':'A fresh start for your knowledge.';$('empty').querySelector('p').textContent=jobs.length?'Try another title or clear your search.':'Add your first source above. Its claims and evidence will appear here.';for(const job of filtered){const button=node('button',undefined,'source-row');button.type='button';const main=node('span',undefined,'source-main');main.append(node('strong',job.title),node('small',`${job.kind==='file'?job.attachment?.name||'File':job.kind==='link'?'Web article':'Text note'} · ${new Date(job.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric'})} · ${job.stage}`));button.append(node('span',icons[job.kind]||'TXT','source-icon'),main,node('span',statusLabels[job.status]||job.status,`status ${job.status}`),node('span','↗','row-arrow'));button.addEventListener('click',()=>openDetail(job.id));$('source-list').append(button);}}
$('search').addEventListener('input',renderList);
function safeURL(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null;}catch{return null;}}
function renderDetail(job){$('detail-title').textContent=job.title;const body=$('detail-body');body.replaceChildren(node('span',`${statusLabels[job.status]||job.status} · ${job.stage}`,`status ${job.status}`),node('p',job.message||'Your original is saved. Processing may take a few minutes.','detail-message'));if(job.attachment){const link=node('a',`Download original · ${job.attachment.name}`,'download');link.href=`/api/uploads/${job.attachment.sha256}`;link.download=job.attachment.name;body.append(link);}for(const link of job.links||[]){if(link.status!=='captured')body.append(node('p',`${link.url}: ${link.reason||link.status}`,'review-note'));}
for(const placed of job.placed||[]){const where=node('p',placed.status==='failed'?placed.note:`Placed among your Ideas: ${placed.claims} claims in ${placed.ideas} Ideas${placed.topics?.length?' · topics: '+placed.topics.map(t=>t.title).join(', '):''}${placed.duplicate_of?' · a repost of an earlier source':''}${placed.note?' · '+placed.note:''}. `,'detail-message');if(placed.status!=='failed'){const a=node('a','See it on the Atlas ↗','download');a.href='/atlas/';where.append(a);}body.append(where);}
if(job.status==='pending'){const retry=node('button','Retry processing','primary');retry.type='button';retry.addEventListener('click',async()=>{retry.disabled=true;retry.textContent='Queuing…';try{await api(`/api/submissions/${job.id}/retry`,{method:'POST',headers:{'Content-Type':'application/json'}});await openDetail(job.id);}catch(e){toast(e.message);retry.disabled=false;retry.textContent='Retry processing';}});body.append(retry);}
for(const result of job.results||[]){const section=node('section',undefined,'detail-section');section.append(node('h3',result.source||'Source'));const url=safeURL(result.url);if(url){const a=node('a','Open original article ↗','download');a.href=url;a.target='_blank';a.rel='noopener noreferrer';section.append(a);}const record=result.processed||{};if(record.quality_status)section.append(node('span',`Review: ${record.quality_status.replaceAll('_',' ')}`,`status ${record.quality_status==='accepted'?'complete':'pending'}`));if(record.triage?.keep===false)section.append(node('p',`Not selected for extraction: ${record.triage.reason}`,'detail-message'));if(record.claims?.length){section.append(node('h3',`${record.claims.length} extracted claims`));for(const claim of record.claims){const card=node('article',undefined,'claim');card.append(node('p',claim.statement),node('div',`${claim.kind} · ${claim.durability_class}`,'claim-meta'));const spans=Array.isArray(claim.evidence)?claim.evidence:[claim.evidence];for(const span of spans){if(span&&Number.isInteger(span.start_char)&&Number.isInteger(span.end_char)){// Backend character offsets count Unicode code points, not JS UTF-16 units.
const quote=Array.from(result.text).slice(span.start_char,span.end_char).join('');card.append(node('blockquote',quote));}}section.append(card);}}else section.append(node('p',job.status==='complete'?'No claims were extracted from this source.':'Claims will appear here when extraction completes.','review-note'));const review=record.reviews?.claims;for(const finding of review?.claims||[]){if(finding.verdict!=='supported'){const claim=record.claims?.find(c=>c.claim_id===finding.claim_id);section.append(node('p',`Needs revision${claim?' · '+claim.statement:''}: ${finding.reason}`,'review-note'));}}if(review?.omitted_concepts?.length)section.append(node('p',`${review.omitted_concepts.length} coverage note(s): the review identified source ideas that may be missing.`,'review-note'));if(review?.summary)section.append(node('p',review.summary,'review-note'));if(record.entities?.length){section.append(node('h3','People & organizations'));const entities=node('div',undefined,'entities');for(const entity of record.entities)entities.append(node('span',`${entity.name} · ${entity.kind}`,'entity'));section.append(entities);}const original=node('details');original.append(node('summary','Read original text'),node('div',result.text,'source-text'));section.append(original);body.append(section);}}
async function openDetail(id){openedId=id;detailVersion='';try{const job=await api(`/api/submissions/${id}`);if(openedId!==id)return;renderDetail(job);detailVersion=JSON.stringify(job);if(!$('detail').open)$('detail').showModal();}catch(e){toast(e.message);}}
$('close-detail').addEventListener('click',()=>$('detail').close());$('detail').addEventListener('close',()=>{openedId=null;});
async function refresh(){try{const next=await api('/api/submissions');const changed=JSON.stringify(next)!==renderedJobs;jobs=next;$('connection-error').hidden=true;if(changed&&!$('source-list').contains(document.activeElement))renderList();if(openedId){const job=await api(`/api/submissions/${openedId}`);const version=JSON.stringify(job);if(version!==detailVersion){const focused=document.activeElement;if(!$('detail-body').contains(focused)){renderDetail(job);detailVersion=version;}}}}catch{$('connection-error').textContent='Cannot reach the workspace. Check that the server is running; your saved sources remain on disk.';$('connection-error').hidden=false;}finally{setTimeout(refresh,2500);}}
refresh();

const graphMotion = document.getElementById('brain-motion');
const graphPause = document.getElementById('brain-pause');
function toggleBrainMotion() {
  const paused = graphMotion.closest('.hero-art').classList.toggle('motion-paused');
  graphMotion.setAttribute('aria-label', paused ? 'Play animated brain knowledge graph' : 'Pause animated brain knowledge graph');
  graphPause.setAttribute('aria-label', paused ? 'Resume brain animation' : 'Pause brain animation');
  graphPause.querySelector('span').textContent = paused ? 'Resume animation' : 'Pause animation';
  graphPause.querySelector('path').setAttribute('d', paused ? 'M5 3l7 5-7 5z' : 'M5 3v10M11 3v10');
  syncBrainAnimation();
}
graphMotion.addEventListener('click', toggleBrainMotion);
graphPause.addEventListener('click', toggleBrainMotion);

$('hero-example').addEventListener('click', () => {
  setMode('text');
  $('example').click();
});

const graphPanel = document.querySelector('.hero-art');
let graphVisible = true;
function updateGraphActivity() {
  graphPanel.classList.toggle('motion-dormant', !graphVisible || document.hidden);
  syncBrainAnimation();
}
new IntersectionObserver(entries => {
  graphVisible = entries[0].isIntersecting;
  updateGraphActivity();
}).observe(graphPanel);
document.addEventListener('visibilitychange', updateGraphActivity);

// Packet positions and node arrival effects share one clock; pausing freezes both.
const brainReducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const brainPackets = [...document.querySelectorAll('.brain-packet')].map(packet => {
  const route = document.getElementById(packet.dataset.route);
  const start = route.getPointAtLength(0);
  packet.setAttribute('transform', `translate(${start.x} ${start.y})`);
  return { packet, route, length: route.getTotalLength(),
    target: document.getElementById(packet.dataset.target),
    duration: Number(packet.dataset.duration), elapsed: Number(packet.dataset.offset) };
});
let brainFrame = null;
let brainLastTime = null;
function syncBrainAnimation() {
  const stopped = brainReducedMotion.matches || graphPanel.classList.contains('motion-paused') || graphPanel.classList.contains('motion-dormant');
  if (stopped) {
    if (brainFrame !== null) cancelAnimationFrame(brainFrame);
    brainFrame = null;
    brainLastTime = null;
  } else if (brainFrame === null) {
    brainFrame = requestAnimationFrame(animateBrain);
  }
}
function animateBrain(time) {
  const delta = brainLastTime === null ? 0 : Math.min(time - brainLastTime, 100);
  brainLastTime = time;
  for (const signal of brainPackets) {
    signal.elapsed += delta;
    if (signal.elapsed >= signal.duration) {
      signal.elapsed %= signal.duration;
      signal.target.classList.add('is-sparking');
    }
    const point = signal.route.getPointAtLength(signal.length * signal.elapsed / signal.duration);
    signal.packet.setAttribute('transform', `translate(${point.x} ${point.y})`);
  }
  brainFrame = requestAnimationFrame(animateBrain);
}
document.querySelectorAll('.brain-flare').forEach(flare => flare.addEventListener('animationend', () => {
  flare.closest('.brain-node').classList.remove('is-sparking');
}));
brainReducedMotion.addEventListener('change', syncBrainAnimation);
syncBrainAnimation();
