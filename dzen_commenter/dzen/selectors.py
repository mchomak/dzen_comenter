"""Единственное место CSS/XPath-селекторов Дзен Студии.

Сейчас — заведомо неокончательные плейсхолдеры (TBD): реальный HTML Дзена ещё
не сохранён. Финализация — этап 3A, который правит ТОЛЬКО этот файл.
Нигде, кроме этого модуля, не должно быть селекторных литералов.
"""

# Форма входа: её наличие на странице = разлогинены.
LOGIN_FORM = "form.login"  # TODO 3A: реальный HTML

# Контейнер одного комментария.
COMMENT_ITEM = ".comment-item"  # TODO 3A: реальный HTML

# Атрибут/подселекторы для извлечения полей комментария.
COMMENT_ID_ATTR = "data-comment-id"  # TODO 3A: реальный HTML
COMMENT_AUTHOR = ".comment-author"  # TODO 3A: реальный HTML
COMMENT_TEXT = ".comment-text"  # TODO 3A: реальный HTML
COMMENT_PARENT_ATTR = "data-parent-id"  # TODO 3A: реальный HTML
COMMENT_POSTED_ATTR = "data-posted-at"  # TODO 3A: реальный HTML

# Публикация ответа.
REPLY_INPUT = ".reply-input"  # TODO 3A: реальный HTML
REPLY_SUBMIT = ".reply-submit"  # TODO 3A: реальный HTML
