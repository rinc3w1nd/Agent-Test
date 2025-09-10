// Deep traversal (crosses shadow roots)
function* walkDeep(root=document) {
  yield root;
  const kids = root.shadowRoot ? [root.shadowRoot, ...root.children] : root.children || [];
  for (const n of kids) yield* walkDeep(n);
}

function deepQueryAll(selector, root=document) {
  const out = [];
  for (const n of walkDeep(root)) {
    if (n.querySelectorAll) {
      try {
        n.querySelectorAll(selector).forEach(el => out.push(el));
      } catch (e) {}
    }
  }
  return out;
}

// Try to extract message "groups" and their author/aria in a tolerant way
function getAllMessages() {
  const groups = deepQueryAll([
    '[data-tid="messageCard"]',
    '[data-tid="message"]',
    '[role="group"]',
    '[data-tid="threadMessage"]',
    '[data-tid="chatMessage"]',
    '[data-tid="post"]'
  ].join(', '));

  const authorSel = [
    '[data-tid="messageAuthorName"]',
    '[data-tid="authorName"]',
    '[data-tid="threadMessageAuthorName"]',
    '[data-tid="postAuthorName"]',
    '[data-tid="chatMessageAuthorName"]'
  ].join(', ');

  const bodySel = [
    '[data-tid="messageBody"]',
    '[data-tid="messageText"]',
    '[data-tid="messageContent"]',
    '[data-tid="adaptiveCardRoot"]'
  ].join(', ');

  const rows = [];
  for (const g of groups) {
    const author = deepQueryAll(authorSel, g)[0];
    const aria = (g.getAttribute?.('aria-label') || '').trim();
    const who = (author?.textContent || '').trim();
    const bodyEl = deepQueryAll(bodySel, g)[0];
    const body = (bodyEl?.innerText || '').trim().slice(0, 160);
    if (who || aria || body) {
      rows.push({ who, aria, body, tag: g.tagName, tids: Array.from(g.querySelectorAll?.('[data-tid]') || []).slice(0,3).map(n => n.getAttribute('data-tid')) });
    }
  }
  return rows;
}

// 1) Dump what we can see (authors + aria)
const dump = getAllMessages();
console.table(dump.map(({who, aria, body}) => ({who, aria: aria.slice(0,120), body})));
dump.length