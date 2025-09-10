// js/extract_last_bot.js
// Returns {text, html, cards[]} from the last message authored by BOT.
(() => {
  const BOT = "BOT_NAME_PLACEHOLDER";
  const out = { text: "", html: "", cards: [] };
  const listSel = '[data-tid="channelMessageList"], [data-tid="threadList"]';
  const groupSel = '[role="group"], [data-tid="messageCard"], [data-tid="message"]';
  const authorSel = '[data-tid="messageAuthorName"], [data-tid="authorName"]';
  const bodySel = '[data-tid="messageBody"], [data-tid="messageText"], [data-tid="adaptiveCardRoot"], [data-tid="messageContent"]';
  const cardSel = '[data-tid="adaptiveCardRoot"]';
  let last = null;
  const roots = document.querySelectorAll(listSel);
  for (const root of roots) {
    const groups = root.querySelectorAll(groupSel);
    for (const g of groups) {
      const authorNode = g.querySelector(authorSel);
      const aria = (g.getAttribute('aria-label') || '');
      const txt = (authorNode?.textContent || '').trim();
      if (txt === BOT || aria.includes(BOT + " app said") || aria.includes(BOT + " posted")) {
        last = g;
      }
    }
  }
  if (!last) return out;
  const body = last.querySelector(bodySel);
  if (body) {
    out.text = body.innerText || body.textContent || "";
    out.html = body.innerHTML || "";
  } else {
    out.text = last.innerText || last.textContent || "";
    out.html = last.innerHTML || "";
  }
  const cards = last.querySelectorAll(cardSel);
  for (const c of cards) {
    out.cards.push({
      text: c.innerText || "",
      html: c.innerHTML || ""
    });
  }
  return out;
})();