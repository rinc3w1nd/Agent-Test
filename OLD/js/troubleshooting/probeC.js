(()=>{const sA='[data-tid="messageAuthorName"],[data-tid="authorName"],[data-tid="threadMessageAuthorName"]';let g=$0.closest('[role="group"],[data-tid="message"],[data-tid="messageCard"]')||$0;
let a=g.querySelector(sA); if(!a){let p=g.previousElementSibling,t=6;while(p&&!a&&t--){a=p.querySelector?.(sA);p=p.previousElementSibling;}}
const who=(a?.textContent||'').trim(); const aria=(g.getAttribute('aria-label')||'').trim();
console.log('WHO',who||'-'); console.log('ARIA',aria?1:0);})();