((I,NAME)=>{const roots=[...document.querySelectorAll('[data-tid="threadList"],[data-tid="channelMessageList"],[data-tid="mainMessageList"]')];
const r=roots[I]||document;const n=(NAME||'').toLowerCase();let hit=0;
const groups=[...r.querySelectorAll('[role="group"],[data-tid="message"],[data-tid="messageCard"]')];
for(const g of groups){
  const aria=(g.getAttribute('aria-label')||'').toLowerCase();
  const text=(g.innerText||'').toLowerCase();
  if(aria.includes(n)||text.includes(n)){hit=1;break;}
}
console.log('NAME',hit);
})(INDEX,'BOTNAME');