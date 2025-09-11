(() => {
  const groups = document.querySelectorAll(
    [
      '[data-tid="messageCard"]',
      '[data-tid="message"]',
      '[role="group"]',
      '[data-tid="threadMessage"]',
      '[data-tid="chatMessage"]',
      '[data-tid="post"]'
    ].join(', ')
  );

  const authorSel = [
    '[data-tid="messageAuthorName"]',
    '[data-tid="authorName"]',
    '[data-tid="threadMessageAuthorName"]',
    '[data-tid="postAuthorName"]',
    '[data-tid="chatMessageAuthorName"]'
  ].join(', ');

  const out = [];
  groups.forEach(g => {
    const author = g.querySelector(authorSel);
    const aria = (g.getAttribute('aria-label') || '').trim();
    const who = (author?.textContent || '').trim();
    const body =
      (g.querySelector('[data-tid="messageBody"], [data-tid="messageText"], [data-tid="messageContent"], [data-tid="adaptiveCardRoot"]')?.innerText || '')
        .trim()
        .slice(0, 120);
    if (who || aria) {
      out.push({ who, aria, body, tag: g.tagName, tids: [...g.querySelectorAll('[data-tid]')].slice(0,3).map(n=>n.getAttribute('data-tid')) });
    }
  });
  out;
})();