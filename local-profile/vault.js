// The backend authenticates the student. Only encrypted application state is persisted here.
const vault = (() => {
  const sessionKey = 'cc.session';
  let account, key, writing, dirty = null, lastSaved = null;
  let writeError = false, busy = false;
  const gate = () => document.querySelector('#account-gate');
  const note = message => { document.querySelector('#account-note').textContent = message; };
  const storageKey = () => 'cc.profile.' + account.id;
  const encode = bytes => {
    let text = '';
    for (let i=0; i<bytes.length; i+=16384) text += String.fromCharCode(...bytes.subarray(i,i+16384));
    return btoa(text);
  };
  const decode = text => Uint8Array.from(atob(text),c=>c.charCodeAt(0));
  async function api(route, body) {
    const headers = {Authorization:'Bearer '+(sessionStorage.getItem(sessionKey)||'')};
    const options = {headers,cache:'no-store'};
    if(body !== undefined){ options.method='POST'; headers['Content-Type']='application/json'; options.body=JSON.stringify(body); }
    const response=await fetch('/api/'+route,options);
    const result=await response.json();
    if(!response.ok){ const e=new Error(result.error || '请求失败'); e.status=response.status; throw e; }
    return result;
  }
  function lock(message){
    // Reload also drops decrypted DOM, callbacks and the in-memory encryption key.
    sessionStorage.removeItem(sessionKey);
    if(message) sessionStorage.setItem('cc.notice',message);
    document.querySelector('#app').hidden=true;
    location.reload();
  }
  async function verify(){
    if(!account) throw new Error('请先登录。');
    try {
      const current=await api('session');
      if(current.id!==account.id || current.generation!==account.generation){lock('档案已发生变化，请重新登录。'); throw new Error('会话已变更。');}
    } catch(e){ if(e.status===401) lock('登录已过期，请重新登录。'); throw e; }
  }
  function setError(value){
    writeError=value;
    const banner=document.querySelector('#vault-error');
    banner.hidden=!value;
  }
  async function writePending(){
    while(dirty!==null){
      const text=dirty; dirty=null;
      try {
        const iv=crypto.getRandomValues(new Uint8Array(12));
        const encrypted=await crypto.subtle.encrypt({name:'AES-GCM',iv},key,new TextEncoder().encode(text));
        localStorage.setItem(storageKey(),JSON.stringify({generation:account.generation,iv:encode(iv),data:encode(new Uint8Array(encrypted))}));
        lastSaved=text; setError(false);
      } catch { if(dirty===null) dirty=text; setError(true); break; }
    }
  }
  function persist(){
    if(!key) return false;
    const text=JSON.stringify(state);
    if(text===lastSaved && !writing && dirty===null) return true;
    dirty=text;
    if(!writing) writing=writePending().finally(()=>{writing=null;});
    return !writeError;
  }
  async function flush(){
    persist();
    await writing;
    if(writeError || dirty!==null) throw new Error('未能保存到本机。请检查浏览器存储空间后重试。');
  }
  async function openAccount(){
    const info=await api('session');
    return new Promise((resolve,reject)=>{
      navigator.locks.request('cc.edit.'+info.id,{ifAvailable:true},async lease=>{
        if(!lease){reject(new Error('该账号已在本浏览器另一个标签页打开。请关闭该页后再试。'));return;}
        try {
          const localKey=await crypto.subtle.importKey('raw',Uint8Array.from(info.key.match(/../g),b=>parseInt(b,16)),'AES-GCM',false,['encrypt','decrypt']);
          const raw=localStorage.getItem('cc.profile.'+info.id);
          let stored=null;
          if(raw){
            const envelope=JSON.parse(raw);
            if(envelope.generation===info.generation){
              const bytes=await crypto.subtle.decrypt({name:'AES-GCM',iv:decode(envelope.iv)},localKey,decode(envelope.data));
              stored=JSON.parse(new TextDecoder().decode(bytes));
            } else localStorage.removeItem('cc.profile.'+info.id);
          }
          restoreState(stored);
          account=info; key=localKey;
          gate().hidden=true; document.querySelector('#app').hidden=false;
          document.querySelector('#account-label').textContent='当前账号 · '+info.name;
          const deleted=sessionStorage.getItem('cc.deleted')===info.id;
          sessionStorage.removeItem('cc.deleted');
          if(deleted){state.module='profile';state.profilePage=3;}
          render();
          if(deleted) toast('档案已清空。你仍登录在原账号，可以重新建档。');
          setTimeout(()=>lock('登录已过期，请重新登录。'),Math.max(0,info.expiry*1000-Date.now()));
          resolve();
          await new Promise(()=>{}); // Browser releases the exclusive edit lock on page exit.
        } catch(e){ reject(e); }
      }).catch(reject);
    });
  }
  async function action(fn, {saveFirst=true,checkSession=true}={}){
    if(busy) return;
    busy=true;
    const app=document.querySelector('#app'); app.inert=true;
    try { if(saveFirst) await flush(); else await writing; if(checkSession) await verify(); await fn(); }
    catch(e){ toast(e.message,'warn'); }
    finally { busy=false; app.inert=false; }
  }
  async function start(){
    if(!/^https?:$/.test(location.protocol) || !['127.0.0.1','localhost'].includes(location.hostname)){note('请在项目目录运行 python server.py，再打开 http://127.0.0.1:8765/。'); return;}
    if(!crypto.subtle || !navigator.locks){note('请通过本机地址使用支持安全存储的现代浏览器。');return;}
    const form=document.querySelector('#account-form');
    form.hidden=false;
    note(sessionStorage.getItem('cc.notice')||'使用自己的账号打开此浏览器中的档案。'); sessionStorage.removeItem('cc.notice');
    form.onsubmit=async event=>{
      event.preventDefault(); if(form.inert) return; form.inert=true;
      try {
        const result=await api(event.submitter?.value==='register'?'register':'login',{name:form.elements.username.value,password:form.elements.password.value});
        form.elements.password.value=''; sessionStorage.setItem(sessionKey,result.token); await openAccount();
      } catch(e){note(e.message);} finally { form.inert=false; }
    };
    if(sessionStorage.getItem(sessionKey)){
      form.inert=true;
      try{ await openAccount(); }catch(e){if(e.status===401) sessionStorage.removeItem(sessionKey);note(e.message);}finally{form.inert=false;}
    }
  }
  window.addEventListener('beforeunload',e=>{if(writing || dirty!==null){e.preventDefault();e.returnValue='';}});
  window.addEventListener('pageshow',e=>{if(e.persisted) location.reload();});
  document.addEventListener('visibilitychange',()=>{if(account && !document.hidden) verify().catch(e=>toast(e.message,'warn'));});
  return {start,persist,flush,action, name:()=>account?.name,
    logout:()=>action(async()=>{
      if(writeError && !confirm('最近的修改未保存。仍要退出账号吗？')) return;
      try { await api('logout',{}); } finally { dirty=null; lock(); }
    }, {saveFirst:false,checkSession:false}),
    erase:()=>action(async()=>{
      await api('profile/delete',{generation:account.generation});
      dirty=null;
      key=null;
      try{localStorage.removeItem(storageKey());}finally{
        sessionStorage.setItem('cc.deleted',account.id);
        location.reload();
      }
    }, {saveFirst:false})};
})();
