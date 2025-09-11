(function(){
  const bodySel = '[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';

  function* walk(n){ 
    yield n; 
    const kids = (n && n.shadowRoot) ? [n.shadowRoot, ...n.children] : (n?.children || []); 
    for (const k of kids) yield* walk(k); 
  }

  function deepQueryOne(sel, root){
    for (const n of walk(root || document)){
      if (n.querySelector){
        try { const el = n.querySelector(sel); if (el) return el; } catch(e){}
      }
    }
    return null;
  }

  function deepText(root){
    let t = '';
    try { t = root.innerText?.trim(); } catch(e){}
    if (!t){ try { t = root.textContent?.trim(); } catch(e){} }
    try {
      const b = deepQueryOne(bodySel, root);
      if (b){
        const bt = (b.innerText || b.textContent || '').trim();
        if (bt) t = bt;
      }
    } catch(e){}
    return t || '';
  }

  // Returns a callable that, given bot name (lowercase), finds the latest message for that author.
  return (botLC) => {
    botLC = (botLC || '').toLowerCase();
    // Greedy list of potential message containers (Teams uses role=group + data-tid variants)
    const groups = Array.from(document.querySelectorAll('[role="group"], [data-tid]'));
    for (let i = groups.length - 1; i >= 0; i--){
      const node = groups[i];
      const aria = (node.getAttribute?.('aria-label') || '').toLowerCase();
      const txt  = (node.textContent || '').toLowerCase();
      const looksMsg = !!(node.getAttribute?.('role') === 'group' || node.getAttribute?.('data-tid'));
      const hit = looksMsg && (aria.includes(botLC) || txt.includes(botLC));
      if (hit){
        const text = deepText(node);
        const html = node.innerHTML || '';
        return { text, html };
      }
    }
    return null;
  };
})()