(()=>{function*W(n){yield n;const c=n.shadowRoot?[n.shadowRoot,...n.children]:(n.children||[]);for(const k of c)yield*W(k)}
const Q=(s,r=document)=>{const o=[];for(const n of W(r))if(n.querySelectorAll){try{n.querySelectorAll(s).forEach(e=>o.push(e))}catch(e){}}return o};
const G='[role="group"],[data-tid="message"],[data-tid="messageCard"],[data-tid="threadMessage"],[data-tid="chatMessage"],[data-tid="post"]';
const A='[data-tid="messageAuthorName"],[data-tid="authorName"],[data-tid="threadMessageAuthorName"]';
const B='[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';
const gs=Q(G).slice(-10);
const out=gs.map((g,i)=>{let a=Q(A,g)[0];if(!a){let p=g.previousElementSibling,t=6;while(p&&!a&&t--){a=Q(A,p)[0];p=p.previousElementSibling}}
const Wf=a?1:0, Af=/\b(app said|posted)\b/i.test((g.getAttribute?.('aria-label')||''))?1:0;
const Bf=((Q(B,g)[0]?.innerText||'').trim())?1:0;return `${i}:${Wf}${Af}${Bf}`;});
console.log('DFLAGS',out.join(' '));})();