// js/count_bot_msgs.js
// Returns the count of messages authored by BOT within both the channel list and thread list.
(() => {
  const BOT = "BOT_NAME_PLACEHOLDER";
  const listSel = '[data-tid="channelMessageList"], [data-tid="threadList"]';
  const groupSel = '[role="group"], [data-tid="messageCard"], [data-tid="message"]';
  const authorSel = '[data-tid="messageAuthorName"], [data-tid="authorName"]';
  let cnt = 0;
  const roots = document.querySelectorAll(listSel);
  for (const root of roots) {
    const groups = root.querySelectorAll(groupSel);
    for (const g of groups) {
      const authorNode = g.querySelector(authorSel);
      const aria = (g.getAttribute('aria-label') || '');
      const txt = (authorNode?.textContent || '').trim();
      if (txt === BOT || aria.includes(BOT + " app said") || aria.includes(BOT + " posted")) {
        cnt++;
      }
    }
  }
  return cnt;
})();