(() => {
  const groups = document.querySelectorAll('[role="group"], [data-tid="message"], [data-tid="messageCard"]');
  let hits = 0;
  groups.forEach(g => {
    const aria = (g.getAttribute('aria-label') || '');
    if (/\bapp said\b|\bposted\b/i.test(aria)) {
      g.style.outline = '3px solid #ffd54f';
      g.style.background = 'rgba(255,213,79,0.1)';
      hits++;
    }
  });
  console.log('Highlighted groups:', hits);
})();