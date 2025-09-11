((I)=>{const roots=[...document.querySelectorAll('[data-tid="threadList"],[data-tid="channelMessageList"],[data-tid="mainMessageList"]')];
const r=roots[I]||document;let a=0;
r.querySelectorAll('[role="group"],[data-tid="message"],[data-tid="messageCard"]').forEach(g=>{
  const x=(g.getAttribute('aria-label')||'').toLowerCase();
  if(x.includes('app said')||x.includes('posted')) a++;
});
console.log('ARIA',a);
})(INDEX);