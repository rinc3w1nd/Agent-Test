(() => {
  const needle = /acmebot/i; // <-- set your bot name here (case-insensitive)
  const groups = document.querySelectorAll('[role="group"], [data-tid="message"], [data-tid="messageCard"]');
  const hits = [];
  groups.forEach(g => {
    const t1 = (g.textContent || '');
    const a1 = (g.getAttribute('aria-label') || '');
    if (needle.test(t1) || needle.test(a1)) {
      g.style.outline = '3px solid #90caf9';
      hits.push({ aria: a1.slice(0,120), text: t1.slice(0,120) });
    }
  });
  hits;
})();