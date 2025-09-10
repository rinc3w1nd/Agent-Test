(()=>{const selG='[role="group"],[data-tid="message"],[data-tid="messageCard"],[data-tid="threadMessage"],[data-tid="chatMessage"],[data-tid="post"]';
const selB='[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';
const gs=[...document.querySelectorAll(selG)];
const G=gs.length, A=gs.filter(g=>((g.getAttribute('aria-label')||'').match(/\b(app said|posted)\b/i))).length;
const B=gs.filter(g=>(g.querySelector(selB)?.innerText||'').trim()).length;
console.log('G',G,'A',A,'B',B);})();