((NAME)=>{function*W(n){yield n;const c=n.shadowRoot?[n.shadowRoot,...n.children]:(n.children||[]);for(const k of c)yield*W(k)}
const Q=(s,r=document)=>{const o=[];for(const n of W(r))if(n.querySelectorAll){try{n.querySelectorAll(s).forEach(e=>o.push(e))}catch(e){}}return o};
const n=(NAME||'').toLowerCase();
const G='[role="group"],[data-tid="message"],[data-tid="messageCard"],[data-tid="threadMessage"],[data-tid="chatMessage"],[data-tid="post"]';
const hit=Q(G).some(g=>{const t=(g.innerText||'').toLowerCase();const a=(g.getAttribute?.('aria-label')||'').toLowerCase();return t.includes(n)||a.includes(n)});
console.log('DNAME',hit?1:0);})('BOTNAME');