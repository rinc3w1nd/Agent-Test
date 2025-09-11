(()=>{const sG='[role="group"],[data-tid="message"],[data-tid="messageCard"],[data-tid="threadMessage"],[data-tid="chatMessage"],[data-tid="post"]';
const sA='[data-tid="messageAuthorName"],[data-tid="authorName"],[data-tid="threadMessageAuthorName"]';
const sB='[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';
const gs=[...document.querySelectorAll(sG)].slice(-10);
const out=gs.map((g,i)=>{let a=g.querySelector(sA);
if(!a){let p=g.previousElementSibling,t=5;while(p&&!a&&t--){a=p.querySelector?.(sA);p=p.previousElementSibling;}}
const W= a?1:0, A=((g.getAttribute('aria-label')||'').match(/\b(app said|posted)\b/i))?1:0;
const B=((g.querySelector(sB)?.innerText||'').trim())?1:0; return `${i}:${W}${A}${B}`;});
console.log('FLAGS',out.join(' '));})();