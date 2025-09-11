(() => {
  const roots = document.querySelectorAll('[data-tid="threadList"], [data-tid="channelMessageList"], [data-tid="mainMessageList"]');
  const res = [];
  roots.forEach((root, idx) => {
    const groups = root.querySelectorAll('[role="group"], [data-tid="message"], [data-tid="messageCard"]');
    groups.forEach(g => {
      const who =
        (g.querySelector('[data-tid="messageAuthorName"], [data-tid="authorName"], [data-tid="threadMessageAuthorName"]')?.textContent || '').trim();
      const aria = (g.getAttribute('aria-label') || '').trim();
      res.push({ rootIndex: idx, who, aria });
    });
  });
  res;
})();