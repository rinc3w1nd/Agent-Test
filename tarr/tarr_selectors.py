# Preferred → fallback list; we'll probe them in order.
COMPOSER_CANDIDATES = [
    '[data-tid="ckeditor-input"] [contenteditable="true"]',
    '[data-tid="messageBodyInput"] [contenteditable="true"]',
    '[data-tid="messageComposer"] [contenteditable="true"]',
    '[data-tid="newMessage"] [contenteditable="true"]',
    # generic fallbacks (scoped to textbox/contenteditable)
    '[role="textbox"][contenteditable="true"]',
    '[contenteditable="true"]',
]

SEND_BUTTON = '[data-tid="send"]'
MENTION_POPUP = '[data-tid="mentionSuggestList"], [role="listbox"]'
MESSAGE_LIST = '[data-tid="mainMessageList"], [data-tid="threadList"], [data-tid="channelMessageList"]'