(()=>{function*W(n){yield n;const c=n.shadowRoot?[n.shadowRoot,...n.children]:(n.children||[]);for(const k of c)yield*W(k)}
const Q=(s,r=document)=>{const o=[];for(const n of W(r))if(n.querySelectorAll){try{n.querySelectorAll(s).forEach(e=>o.push(e))}catch(e){}}
return o};
const GSEL='[role="group"],[data-tid="message"],[data-tid="messageCard"],[data-tid="threadMessage"],[data-tid="chatMessage"],[data-tid="post"]';
const BSEL='[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';
const GS=Q(GSEL), DG=GS.length;
const DA=GS.filter(x=>/\b(app said|posted)\b/i.test((x.getAttribute?.('aria-label')||''))).length;
const DB=GS.filter(x=>{const el=Q(BSEL,x)[0];return !!(el&&((el.innerText||'').trim()))}).length;
console.log('DG',DG,'DA',DA,'DB',DB);})();