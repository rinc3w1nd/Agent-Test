COMPOSER_CANDIDATES = [
    '[data-tid="ckeditor-input"] [contenteditable="true"]',
    '[data-tid="messageBodyInput"] [contenteditable="true"]',
    '[data-tid="messageComposer"] [contenteditable="true"]',
    '[data-tid="newMessage"] [contenteditable="true"]',
    '[role="textbox"][contenteditable="true"]',
    '[contenteditable="true"]',
]

SEND_BUTTON   = '[data-tid="send"]'

MENTION_POPUP = '[data-tid="mentionSuggestList"], [role="listbox"]'
MENTION_OPTION = (
    '[data-tid="mentionSuggestList"] [role="option"], '
    '[role="listbox"] [role="option"], '
    '[data-tid="mentionSuggestList"] li, '
    '[role="listbox"] li'
)

MENTION_PILL = (
    'span[data-mention-id], '
    'span[data-mention-entity-id], '
    'span[data-tid="mention"], '
    'span.mention, '
    'span[contenteditable="false"][data-mention-id]'
)

MESSAGE_LIST  = '[data-tid="mainMessageList"], [data-tid="threadList"], [data-tid="channelMessageList"]'