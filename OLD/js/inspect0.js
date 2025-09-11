// Inspect around the selected message element ($0) Use CMD+Shift+C to select the element first
(function inspect(el){
  if(!el){ console.warn("Pick a bot message bubble with the element picker so $0 is set."); return; }

  // Walk up a few ancestors and show attributes
  let n = el, rows = [];
  for (let i=0; n && i<8; i++, n=n.parentElement) {
    rows.push({
      tag: n.tagName,
      id: n.id || "",
      class: n.className || "",
      role: n.getAttribute?.("role") || "",
      tid: n.getAttribute?.("data-tid") || "",
      ariaLabel: n.getAttribute?.("aria-label") || "",
      ariaLabelledby: n.getAttribute?.("aria-labelledby") || "",
      text: (n.innerText || "").trim().slice(0, 120)
    });
  }
  console.table(rows);

  // Resolve aria-labelledby → element text if present
  function resolveLabel(node){
    const al = node.getAttribute?.("aria-labelledby");
    if (!al) return "";
    const id = al.split(/\s+/)[0];
    const lab = document.getElementById(id);
    return (lab?.innerText || "").trim();
  }
  let labelled = "";
  n = el;
  for (let i=0; n && i<8 && !labelled; i++, n=n.parentElement) labelled = resolveLabel(n);
  console.log("Resolved aria-labelledby text:", labelled);

  // Hunt nearby for author text
  const authorSel = [
    '[data-tid*="Author"]',
    '[data-tid="authorName"]',
    '[data-tid="messageAuthorName"]',
    '[data-tid="threadMessageAuthorName"]',
    '[id*="author"]',
    '[aria-label*=" app said"]',
    '[aria-label*=" posted"]'
  ].join(", ");

  const group = el.closest('[role="group"], [data-tid="message"], [data-tid="messageCard"]') || el;
  let a = group.querySelector(authorSel);
  if (!a) {
    // check siblings above
    let prev = group.previousElementSibling, tries = 5;
    while (prev && tries--) {
      a = prev.querySelector?.(authorSel);
      if (a) break;
      prev = prev.previousElementSibling;
    }
  }
  console.log("Nearby author text:", (a?.textContent || "").trim());
})( $0 );