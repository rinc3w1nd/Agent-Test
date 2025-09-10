((I)=>{const roots=[...document.querySelectorAll('[data-tid="threadList"],[data-tid="channelMessageList"],[data-tid="mainMessageList"]')];
const r=roots[I]||document;let b=0;
const body='[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';
r.querySelectorAll('[role="group"],[data-tid="message"],[data-tid="messageCard"]').forEach(g=>{
  const el=g.querySelector(body); const t=(el?.innerText||'').trim();
  if(t) b++;
});
console.log('BODY',b);
})(INDEX);