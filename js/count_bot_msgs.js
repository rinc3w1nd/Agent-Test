// js/count_bot_msgs.js
(() => {
  const BOT = "BOT_NAME_PLACEHOLDER";
  const botLC = BOT.toLowerCase();

  const listSel  = '[data-tid="channelMessageList"], [data-tid="threadList"], [data-tid="mainMessageList"]';
  const groupSel = '[role="group"], [data-tid="messageCard"], [data-tid="message"], [data-tid="threadMessage"], [data-tid="chatMessage"], [data-tid="post"]';
  const authorSel = [
    '[data-tid="messageAuthorName"]',
    '[data-tid="authorName"]',
    '[data-tid="threadMessageAuthorName"]',
    '[data-tid="postAuthorName"]',
    '[data-tid="chatMessageAuthorName"]',
    '[id*="author"]'
  ].join(', ');

  function resolveAriaLabelledby(el) {
    const al = el.getAttribute && el.getAttribute('aria-labelledby');
    if (!al) return '';
    const id = al.split(/\s+/)[0];
    const lab = document.getElementById(id);
    return (lab && lab.innerText || '').trim();
  }

  function findNearbyAuthor(group) {
    // 1) Author inside the group
    let a = group.querySelector(authorSel);
    if (a) return (a.textContent || '').trim();

    // 2) Walk up siblings above to find a header with the author
    let prev = group.previousElementSibling, tries = 5;
    while (prev && tries--) {
      a = prev.querySelector && prev.querySelector(authorSel);
      if (a) return (a.textContent || '').trim();
      prev = prev.previousElementSibling;
    }

    // 3) aria-labelledby target
    const lbl = resolveAriaLabelledby(group);
    if (lbl) return lbl;

    return '';
  }

  function isBotGroup(group) {
    const who = findNearbyAuthor(group);
    const aria = (group.getAttribute && group.getAttribute('aria-label') || '').trim();
    const whoLC = (who || '').toLowerCase();

    if (whoLC && whoLC.startsWith(botLC)) return true;
    if (aria) {
      const ariaLC = aria.toLowerCase();
      if (ariaLC.includes(botLC) && (ariaLC.includes('app said') || ariaLC.includes('posted'))) return true;
    }
    return false;
  }

  let cnt = 0;
  const roots = document.querySelectorAll(listSel);
  for (const root of roots) {
    const groups = root.querySelectorAll(groupSel);
    for (const g of groups) {
      if (isBotGroup(g)) cnt++;
    }
  }
  return cnt;
})();