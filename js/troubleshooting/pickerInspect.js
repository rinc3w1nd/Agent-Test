(()=>{function*W(n){yield n;const c=n.shadowRoot?[n.shadowRoot,...n.children]:(n.children||[]);for(const k of c)yield*W(k)}
const Q=(s,r=document)=>{const o=[];for(const n of W(r))if(n.querySelectorAll){try{n.querySelectorAll(s).forEach(e=>o.push(e))}catch(e){}}return o};
const A='[data-tid="messageAuthorName"],[data-tid="authorName"],[data-tid="threadMessageAuthorName"]';
const B='[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';
const g=$0.closest?.('[role="group"],[data-tid="message"],[data-tid="messageCard"]')||$0;
const who=(Q(A,g)[0]?.innerText||'').trim(); const body=(Q(B,g)[0]?.innerText||'').trim();
const aria=(g.getAttribute?.('aria-label')||'').trim();
console.log('DWHO',who||'-'); console.log('DARIA',aria?1:0); console.log('DBLEN',body.length);})();