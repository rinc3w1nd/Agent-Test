[...document.querySelectorAll('[data-tid="messageCard"], [role="group"]')]
  .map(g => {
    const a = g.querySelector('[data-tid="messageAuthorName"], [data-tid="authorName"], [data-tid="threadMessageAuthorName"]');
    return (a?.textContent || 'NO AUTHOR').trim();
  });