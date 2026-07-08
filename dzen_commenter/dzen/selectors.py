"""Единственное место CSS/XPath-селекторов Дзен Студии.

Селекторы страницы «Комментарии» финализированы по реальному отрендеренному HTML
(`reply.html`, этап 5A). Приоритет — `data-testid`/`aria-label`; там, где их нет —
селектор по префиксу CSS-модульного класса без хэш-суффикса.
Нигде, кроме этого модуля, не должно быть селекторных литералов.
"""

# Форма входа: её наличие на странице = разлогинены.
# Реального HTML логин-формы Студии в снимках нет — финализация в Волне 5B.
LOGIN_FORM = "form.login"  # TODO 5B: реальный HTML логин-формы

# Группа: один пост + все его комментарии.
POST_GROUP = '[data-testid="comment"]'
# Ссылка на пост внутри группы (href вида "/a/<id>").
POST_LINK = '.editor--comments-page__postContainer a[href^="/a/"]'

# Отдельный комментарий внутри группы.
COMMENT_NODE = '[class*="editor--comment__block-"]'
# Ссылка на автора (href вида "/user/<id>").
COMMENT_AUTHOR_LINK = 'a[class*="editor--comment__nameLink-"]'
# Имя автора (текст).
COMMENT_AUTHOR_TEXT = '[class*="editor--comment__nameText-"]'
# Текст комментария.
COMMENT_TEXT = 'p[aria-label="Текст комментария"]'
# Относительная дата, напр. "8 мин".
COMMENT_DATE_TEXT = '[class*="editor--common-date__date-"] span'

# Открывает форму ответа ниже узла комментария.
COMMENT_REPLY_BUTTON = '[data-testid="reply-button"]'
# Контейнер формы ответа (появляется после клика reply-button).
COMMENT_FORM_CONTAINER = '[data-testid="comment-form-container"]'

# Публикация ответа.
REPLY_INPUT = '[data-testid="comment-textarea"]'
REPLY_SUBMIT = '[data-testid="send-button"]'
