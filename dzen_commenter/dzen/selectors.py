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
YANDEX_ID_USERNAME_INPUT = (
    '#passp-field-login, '
    'input[name="login"], '
    'input[autocomplete="username"], '
    'input[data-t="field:input-login"]'
)
YANDEX_ID_PHONE_TAB = (
    'xpath=//*[self::button or self::span or self::div or @role="button" or @role="tab"]'
    '[normalize-space()="Phone number" or normalize-space()="Телефон"]'
)
YANDEX_ID_MORE_BUTTON = (
    'button:has-text("More"), '
    '[role="button"]:has-text("More"), '
    'button:has-text("\u0415\u0449\u0451"), '
    '[role="button"]:has-text("\u0415\u0449\u0451")'
)
YANDEX_ID_USERNAME_LOGIN = (
    'button:has-text("Log in with username"), '
    '[role="button"]:has-text("Log in with username"), '
    'a:has-text("Log in with username"), '
    '[role="menuitem"]:has-text("Log in with username"), '
    '[data-react-aria-pressable="true"]:has-text("Log in with username"), '
    'button:has-text("\u0412\u043e\u0439\u0442\u0438 \u043f\u043e \u043b\u043e\u0433\u0438\u043d\u0443"), '
    '[role="button"]:has-text("\u0412\u043e\u0439\u0442\u0438 \u043f\u043e \u043b\u043e\u0433\u0438\u043d\u0443"), '
    '[role="menuitem"]:has-text("\u0412\u043e\u0439\u0442\u0438 \u043f\u043e \u043b\u043e\u0433\u0438\u043d\u0443"), '
    '[data-react-aria-pressable="true"]:has-text("\u0412\u043e\u0439\u0442\u0438 \u043f\u043e \u043b\u043e\u0433\u0438\u043d\u0443")'
)
YANDEX_ID_USERNAME_LOGIN_XPATH = (
    'xpath=//*[self::button or self::a or self::div or @role="button" '
    'or @role="menuitem" or @data-react-aria-pressable="true"]'
    '[normalize-space(.)="Log in with username" '
    'or normalize-space(.)="\u0412\u043e\u0439\u0442\u0438 \u043f\u043e \u043b\u043e\u0433\u0438\u043d\u0443"]'
)
YANDEX_ID_USERNAME_LOGIN_SELECTORS = (
    YANDEX_ID_USERNAME_LOGIN,
    YANDEX_ID_USERNAME_LOGIN_XPATH,
)
YANDEX_ID_CONTINUE = (
    'button[type="submit"], '
    'button:has-text("Next"), '
    '[role="button"]:has-text("Next"), '
    'button:has-text("Log in"), '
    '[role="button"]:has-text("Log in"), '
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
# Экран Яндекс ID «Выберите аккаунт» (pwl · компонент SplitAddUser/ChooseAddUser).
# Реальная разметка страницы passport.yandex.ru/pwl-yandex/auth/suggest: русские подписи
# («Плюс»/«Карты»/«Семья», «Нет нужного аккаунта»), интерактивные элементы помечены
# data-react-aria-pressable; атрибутов data-t на странице НЕТ. На экране ввода телефона той
# же страницы строка «Выберите аккаунт» встречается только внутри <script> (i18n-бандл), в
# видимом DOM её нет — поэтому детектор привязан к заголовку (h1..h4/role=heading) и к
# <button>, а НЕ к «//*», иначе он ложно срабатывал бы на экране ввода телефона.
YANDEX_ID_ACCOUNT_CHOICE = (
    'xpath=//*[self::h1 or self::h2 or self::h3 or self::h4 or @role="heading"]'
    '[contains(normalize-space(), "Выберите аккаунт") '
    'or contains(normalize-space(), "Select an account")] '
    '| //button[contains(normalize-space(), "Нет нужного аккаунта") '
    'or contains(normalize-space(), "No account")]'
)
YANDEX_ID_ACCOUNT_CARD = (
    '[data-testid="account-list-item"], '
    '[data-testid*="account-item" i], '
    '[data-testid*="account" i][role="button"], '
    '[role="button"]:has-text("Плюс"), '
    'button:has-text("Плюс"), '
    '[role="button"]:has-text("Карты"), '
    'button:has-text("Карты")'
)
# Кликабельная карточка аккаунта: первый интерактивный элемент ПОСЛЕ заголовка
# «Выберите аккаунт», не являющийся вторичными кнопками («Нет нужного аккаунта», «Ещё»,
# «Добавить», QR, вход по лицу/отпечатку) и не содержащий <input> (иначе матчится
# телефонное поле phone-input, из-за чего бот возвращался к вводу номера).
YANDEX_ID_ACCOUNT_CARD_XPATH = (
    'xpath=//*[self::h1 or self::h2 or self::h3 or self::h4 or @role="heading"]'
    '[contains(normalize-space(), "Выберите аккаунт") '
    'or contains(normalize-space(), "Select an account")]'
    '/following::*[(self::button or self::a or @role="button" '
    'or @data-react-aria-pressable="true") '
    'and not(.//input) '
    'and not(contains(normalize-space(), "Нет нужного")) '
    'and not(contains(normalize-space(), "No account")) '
    'and not(contains(normalize-space(), "Ещё")) '
    'and not(contains(normalize-space(), "More")) '
    'and not(contains(normalize-space(), "Добавить")) '
    'and not(contains(normalize-space(), "отпечат")) '
    'and not(contains(normalize-space(), "лицу"))][1]'
)
YANDEX_ID_ACCOUNT_SELECTORS = (
    YANDEX_ID_ACCOUNT_CARD,
    YANDEX_ID_ACCOUNT_CARD_XPATH,
)
# Промо «Войти по лицу/отпечатку» после выбора аккаунта. Нужно закрыть кнопкой
# «Напомнить позже» / «Remind me later», а не «Let's do it!» / «Войти» — иначе бот
# подключит биометрию вместо обычного продолжения логина.
YANDEX_WEBAUTHN_PROMO_DISMISS = (
    'button:has-text("Remind me later"), '
    '[role="button"]:has-text("Remind me later"), '
    'button:has-text("Напомнить позже"), '
    '[role="button"]:has-text("Напомнить позже")'
)
VK_PASSWORD_SUBMIT = (
    'button[type="submit"], '
    'button:has-text("Next"), '
    '[role="button"]:has-text("Next"), '
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
POST_LINK = '[class*="editor--comments-page__postContainer-"] a[href^="/a/"]'

POST_TITLE = '[class*="editor--comment-post__title-"]'

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
