/* Source-bound local review and explicitly requested paid updates. No model call on page load. */
'use strict';
window.peopleReview = (() => {
  const node = (tag,text,cls) => {const e=document.createElement(tag); if(text != null)e.textContent=text; if(cls)e.className=cls; return e;};
  async function api(path,body) {
    const response=await fetch(path,body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Refresher':'1'},body:JSON.stringify(body)});
    const result=await response.json(); if(!response.ok)throw new Error(result.error || 'Request failed.'); return result;
  }
  function input(label,type='text') {
    const box=node('label',null,'people-field'), title=node('span',label), field=node('input'); field.type=type;
    box.append(title,field); return {box,field};
  }
  function reload(claim) {
    if(window.parent !== window) {window.parent.location.reload(); return;}
    const url=new URL(location.href); if(claim){url.searchParams.set('claim',claim);url.searchParams.set('mode','claims');}
    location.assign(url);
  }
  function mount(target,claim) {
    const details=node('details',null,'people-review'); details.append(node('summary','Review speaker and evidence origin'));
    const content=node('div'), message=node('p',null,'small quiet'); message.setAttribute('role','status'); details.append(content,message); target.append(details);
    let loaded=false;
    details.addEventListener('toggle',async()=>{
      if(!details.open || loaded)return; loaded=true; message.textContent='Loading original source passages…';
      try {
        const context=await api('/api/people/attribution?claim='+encodeURIComponent(claim.id));
        const current=context.review || context.suggestion;
        content.append(node('p','Check who actually states this claim. Being named in the passage alone is insufficient. Source acceptance and speaker review are separate.','small quiet'));
        if(context.stale_review)content.append(node('p','An older review no longer matches the source or model attribution. Review again.','chip warn'));
        const who=node('select'), whoLabel=node('label','Who states this?','people-field');
        const unknown=node('option','Unknown / no supported speaker'); unknown.value='';who.append(unknown);
        for(const p of context.people){const o=node('option',p.name+(p.kind==='author'?' (document author)':''));o.value=p.id;who.append(o);}
        who.value=current.person || '';whoLabel.append(who);content.append(whoLabel);
        const passages=node('fieldset');passages.append(node('legend','Original passages — select evidence for this speaker'));
        for(const p of context.passages){
          const label=node('label',null,'people-passage'),check=node('input');check.type='checkbox';check.value=p.id;check.checked=current.passages.includes(p.id);
          label.append(check,node('span',`${p.id}${p.supports_claim?' · supports this claim':''}: ${p.text}`));passages.append(label);
        }
        content.append(passages);
        const profile=input('Public profile URL (optional) — use the same URL for the same person','url'); profile.field.value=current.identity_url || '';
        content.append(profile.box,node('p','Different profile URLs keep same-name people separate. Leave empty when identity is unresolved. This does not search the web.','small quiet'));
        const origin=input('Primary evidence origin URL (optional)','url');origin.field.value=current.origin_url || '';content.append(origin.box);
        const checked=input('I checked the primary evidence origin; this is not just a repost URL.','checkbox');checked.field.checked=current.origin_checked;content.append(checked.box);
        content.append(node('p','Reports repeating the same release should use the same origin URL. Track record increases only when reviewed speakers have distinct checked origins.','small quiet'));
        const save=node('button','Save speaker review','pill'), reset=node('button','Use model attribution again','text-button'); save.type=reset.type='button';
        content.append(save); if(context.review || context.stale_review)content.append(reset);
        async function submit(action){
          save.disabled=reset.disabled=true;message.textContent='Saving review…';
          const chosen=context.people.find(p=>p.id===who.value);
          const review={kind:chosen?.kind || 'unknown',person:chosen?.id || null,
            passages:chosen?[...passages.querySelectorAll('input:checked')].map(e=>Number(e.value)):[],
            identity_url:profile.field.value.trim() || null,origin_url:origin.field.value.trim() || null,origin_checked:checked.field.checked};
          try {await api('/api/people/attribution',{claim_id:claim.id,token:context.token,action,review});reload(claim.id);}
          catch(error){message.textContent=error.message;save.disabled=reset.disabled=false;}
        }
        save.addEventListener('click',()=>submit('review'));reset.addEventListener('click',()=>submit('reset'));
        message.textContent=context.review?'A saved local review is active.':'The model suggestion is provisional until you check and save it.';
      } catch(error){message.textContent=error.message;loaded=false;}
    });
  }
  function backfill(target){
    const details=node('details',null,'people-review');details.append(node('summary','Update attribution in older documents'));
    const box=node('div'),message=node('p',null,'small quiet');message.setAttribute('role','status');details.append(box,message);target.append(details);
    let loaded=false;
    async function poll(id){
      try{const job=await api('/api/people/backfill/'+id);message.textContent=`${job.message} ${job.completed.length}/${job.documents.length} documents saved · ${job.calls} model calls · ${job.spent_tokens} tokens recorded.`;
        if(['queued','processing'].includes(job.status))setTimeout(()=>poll(id),1500);
        else {const refresh=node('button','Refresh graph','pill');refresh.addEventListener('click',()=>reload());box.append(refresh);loaded=false;}
      }catch(error){message.textContent=error.message;loaded=false;}
    }
    details.addEventListener('toggle',async()=>{
      if(!details.open || loaded)return;loaded=true;box.replaceChildren();message.textContent='Checking saved documents; no paid call is running.';
      try{
        const preview=await api('/api/people/backfill');
        if(preview.active_job){poll(preview.active_job.id);return;}
        box.append(node('p',`${preview.documents.length} eligible documents. Select up to ${preview.max_documents}. Maximum ${preview.max_tokens.toLocaleString()} budgeted tokens and ${preview.max_seconds/60} minutes per batch. One model call per document; provider network retries may occur. No currency estimate is available.`, 'small quiet'));
        if(preview.model)box.append(node('p','Model: '+preview.model,'small quiet'));
        box.append(node('p','This updates relation judgments and inferred speakers for selected documents. Originals and source acceptance stay unchanged. Changed model attribution can make an older speaker review stale.','small quiet'));
        const list=node('div',null,'people-update-list');
        for(const doc of preview.documents){const label=node('label',null,'people-passage'),check=node('input');check.type='checkbox';check.value=doc.item_id;
          label.append(check,node('span',`${doc.title} · ${doc.claims} claims · ${doc.characters.toLocaleString()} characters`));list.append(label);}
        box.append(list);
        const run=node('button','Run paid update for selected documents','pill');run.disabled=true;box.append(run);
        list.addEventListener('change',()=>{const n=list.querySelectorAll('input:checked').length;run.disabled=!preview.ready || n<1 || n>preview.max_documents;});
        run.addEventListener('click',async()=>{run.disabled=true;
          try{const job=await api('/api/people/backfill',{token:preview.token,item_ids:[...list.querySelectorAll('input:checked')].map(e=>e.value),confirm_paid:true});list.querySelectorAll('input').forEach(e=>e.disabled=true);poll(job.id);}
          catch(error){message.textContent=error.message;loaded=false;}
        });
        message.textContent=preview.documents.length?preview.message:'All eligible documents have current attribution records.';
      }catch(error){message.textContent=error.message;loaded=false;}
    });
  }
  return {mount,backfill};
})();
