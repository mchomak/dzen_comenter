"""Единственное место CSS/XPath-селекторов Дзен Студии.

Селекторы страницы «Комментарии» финализированы по реальному отрендеренному HTML
(`artifacts/dzen-html/reply.html`, этап 5A). Приоритет — `data-testid`/`aria-label`; там, где их нет —
селектор по префиксу CSS-модульного класса без хэш-суффикса.
Нигде, кроме этого модуля, не должно быть селекторных литералов.
"""

# Авторизация Dzen -> VK ID.
LOGIN_FORM = (
    '[data-testid="login-content-popup"], '
    '[data-testid="login-by-phone-vk-id"], '
    'form.login, '
    'input[type="tel"][autocomplete="on"], '
    'button[aria-label="Войти"], '
    'a[aria-label="Войти"], '
    'button:has-text("Войти"), '
    '[role="button"]:has-text("Войти")'
)

LOGIN_BUTTON = (
    'button[aria-label="Войти"], '
    'a[aria-label="Войти"], '
    'button:has-text("Войти"), '
    '[role="button"]:has-text("Войти")'
)
LOGIN_POPUP = '[data-testid="login-content-popup"]'
LOGIN_PHONE_INPUT = '[data-testid="login-by-phone-vk-id"] input[type="tel"], input[type="tel"]'
LOGIN_PHONE_CONTINUE = (
    '[data-testid="login-by-phone-vk-id"] button[aria-label="Продолжить"], '
    '[data-testid="login-by-phone-vk-id"] button:has-text("Продолжить")'
)
YANDEX_ID_LOGIN = (
    'a[aria-label="Войти через Яндекс ID"], '
    'button[aria-label="Войти через Яндекс ID"], '
    'a[href*="login-yandex-id"], '
    'button:has-text("Войти через Яндекс ID"), '
    '[role="button"]:has-text("Войти через Яндекс ID")'
)
YANDEX_ID_LOGIN_INPUT = (
    '#passp-field-login, '
    'input[name="login"], '
    'input[type="tel"], '
    'input[autocomplete="tel"], '
    'input[inputmode="tel"], '
    'input[inputmode="numeric"], '
    'input[autocomplete="username"], '
    'input[data-t="field:input-login"]'
)
YANDEX_ID_PHONE_TAB = (
    'xpath=//*[self::button or self::span or self::div or @role="button" or @role="tab"]'
    '[normalize-space()="Phone number" or normalize-space()="Телефон"]'
)
YANDEX_ID_CONTINUE = (
    'button[type="submit"], '
    'button:has-text("Next"), '
    '[role="button"]:has-text("Next"), '
    'button:has-text("Продолжить"), '
    '[role="button"]:has-text("Продолжить"), '
    'button:has-text("Войти"), '
    '[role="button"]:has-text("Войти")'
)
VK_PASSWORD_METHOD = (
    'button:has-text("Ввести пароль"), '
    '[role="button"]:has-text("Ввести пароль"), '
    'button:has-text("Пароль"), '
    '[role="button"]:has-text("Пароль")'
)
VK_PASSWORD_INPUT = (
    'input[type="password"], '
    'input[name="password"], '
    'input[name="passwd"], '
    '#passp-field-passwd, '
    'input[autocomplete="current-password"]'
)
AUTH_CODE_INPUT = (
    'input[autocomplete="one-time-code"], '
    'input[name*="code" i], '
    'input[id*="code" i], '
    'input[data-t*="code" i], '
    'input[maxlength="1"]'
)
YANDEX_ID_ACCOUNT_CARD = (
    '[data-t="account-card"], '
    '[data-t="user-account"], '
    '[data-t="account"], '
    '[data-testid*="account" i][role="button"], '
    '[role="button"]:has-text("Plus"), '
    '[role="button"]:has-text("Cards"), '
    'button:has-text("Plus"), '
    'button:has-text("Cards")'
)
YANDEX_ID_ACCOUNT_CARD_XPATH = (
    'xpath=//*[contains(normalize-space(), "Select an account") '
    'or contains(normalize-space(), "Выберите аккаунт")]'
    '/following::*[(self::button or self::a or @role="button" or @data-t '
    'or contains(translate(@class, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "account")) '
    'and not(contains(normalize-space(), "missing")) '
    'and not(contains(normalize-space(), "не найден"))][1]'
)
YANDEX_ID_ACCOUNT_SELECTORS = (
    YANDEX_ID_ACCOUNT_CARD,
    YANDEX_ID_ACCOUNT_CARD_XPATH,
)
VK_PASSWORD_SUBMIT = (
    'button[type="submit"], '
    'button:has-text("Продолжить"), '
    '[role="button"]:has-text("Продолжить"), '
    'button:has-text("Войти"), '
    '[role="button"]:has-text("Войти")'
)
VK_ALLOW_ACCESS = (
    'button:has-text("Разрешить"), '
    '[role="button"]:has-text("Разрешить"), '
    'button:has-text("Продолжить"), '
    '[role="button"]:has-text("Продолжить")'
)
AUTH_CAPTCHA = (
    'input[name="captcha"], '
    'input[aria-label*="капч" i], '
    '[data-testid*="captcha"], '
    '[class*="captcha" i], '
    'img[alt*="captcha" i], '
    'img[src*="captcha" i]'
)

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
