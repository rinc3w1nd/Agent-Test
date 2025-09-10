const needle = /AcmeBot/i; // <-- change this
let hits = 0;
for (const g of deepQueryAll('[role="group"], [data-tid="message"], [data-tid="messageCard"]')) {
  const t = (g.textContent || '');
  const a = (g.getAttribute?.('aria-label') || '');
  if (needle.test(t) || needle.test(a)) {
    g.style.outline = '3px solid #90caf9';
    g.style.background = 'rgba(144,202,249,0.15)';
    console.log('HIT:', (a||t).slice(0,160));
    hits++;
  }
}
console.log('Total hits:', hits);