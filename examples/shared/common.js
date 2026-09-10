export const $ = (s) => document.querySelector(s);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const config = {};
export const pretty = s => String(s??'').replaceAll('_',' ');
export const badge = (s, kind='') => `<span class="badge ${kind}">${esc(s)}</span>`;
export const metric = (name,value,note='') => `<article class="metric"><span>${esc(name)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`;
export const hero = (eyebrow,title,description) => `<div class="hero"><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p>${esc(description)}</p></div>`;
export function status(message,kind='success') {const node=$('#status');node.hidden=!message;node.className='notice '+kind;node.textContent=message;}
export async function api(path,body,token) {
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),45000);
  const headers={};if(token)headers.Authorization='Bearer '+token;
  if(body!==undefined){headers['Content-Type']='application/json';headers['X-Lab-Token']=config.csrfToken;}
  try {const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
    const data=await response.json();if(!response.ok)throw new Error(`HTTP ${response.status}: ${data.error||'Request failed'}`);return data;
  } finally {clearTimeout(timeout);}
}
export async function refreshConfig(){Object.assign(config,await api('/api/config'));$('#live-status').textContent=config.live.enabled?`Live AI enabled · ${config.live.remainingCalls} calls left`:'Sample mode · no model calls';}
let busy=false;
export async function perform(fn){if(busy)return;busy=true;status('');const states=[...document.querySelectorAll('button')].map(b=>[b,b.disabled]);states.forEach(([b])=>b.disabled=true);document.body.setAttribute('aria-busy','true');try{await fn();}catch(error){status(error.name==='AbortError'?'Request timed out. Inspect the result before retrying.':error.message,'error');}finally{states.forEach(([b,disabled])=>b.disabled=disabled);document.body.removeAttribute('aria-busy');busy=false;}}
export async function download(name,data){try{const result=await api('/api/export',{name,data});const a=document.createElement('a');a.href=result.url;a.download=name;document.body.append(a);a.click();a.remove();}catch(error){status(error.message,'error');}}
export const modelOptions = () => config.live.models.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');
export const liveDisabled = () => !config.live.enabled||config.live.remainingCalls<=0;
