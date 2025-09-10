// Collect every message container and print what "author" it shows
[...document.querySelectorAll('[data-tid="messageCard"], [role="group"], [data-tid="message"]')]
  .map(g => {
    const author =
      g.querySelector('[data-tid="messageAuthorName"], [data-tid="authorName"], [data-tid="threadMessageAuthorName"]');
    const aria = g.getAttribute("aria-label") || "";
    return {
      textContent: (author?.textContent || "").trim(),
      ariaLabel: aria.trim().slice(0, 120) // trim long aria labels
    };
  })
  .filter(x => x.textContent || x.ariaLabel)