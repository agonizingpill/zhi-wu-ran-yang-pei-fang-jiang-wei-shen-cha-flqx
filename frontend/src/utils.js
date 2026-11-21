export const escape=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const decimal=(x,n=2)=>Number(x).toFixed(n);
export function notify(message){const el=document.querySelector('#notice');el.textContent=message;el.classList.add('show');clearTimeout(window.noticeTimer);window.noticeTimer=setTimeout(()=>el.classList.remove('show'),4500);}
export function formData(form){return Object.fromEntries(new FormData(form));}
export const wait=ms=>new Promise(r=>setTimeout(r,ms));
export const requestId=()=>globalThis.crypto?.randomUUID?crypto.randomUUID():`req-${Date.now()}-${Math.random().toString(36).slice(2,10)}`;

export const localTime=x=>new Date(x+'Z').toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
