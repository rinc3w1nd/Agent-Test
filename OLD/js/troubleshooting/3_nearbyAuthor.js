((I)=>{const roots=[...document.querySelectorAll('[data-tid="threadList"],[data-tid="channelMessageList"],[data-tid="mainMessageList"]')];
const r=roots[I]||document;let c=0;
const sel='[data-tid="messageAuthorName"],[data-tid="authorName"],[data-tid="threadMessageAuthorName"]';
[...r.querySelectorAll('[role="group"],[data-tid="message"],[data-tid="messageCard"]')].forEach(g=>{
  let a=g.querySelector(sel);
  if(!a){let p=g.previousElementSibling,t=5;while(p&&!a&&t--){a=p.querySelector?.(sel);p=p.previousElementSibling;}}
  if(a) c++;
});
console.log('AUTH',c);
})(INDEX);