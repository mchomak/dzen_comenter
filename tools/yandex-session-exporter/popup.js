const allowedDomains = ["dzen.ru", "yandex.ru", "vk.ru"];
const status = document.getElementById("status");

function isAllowedDomain(domain) {
  const normalized = domain.replace(/^\./, "");
  return allowedDomains.some(
    (allowed) => normalized === allowed || normalized.endsWith(`.${allowed}`),
  );
}

function toPlaywrightCookie(cookie) {
  const sameSite = {
    unspecified: undefined,
    no_restriction: "None",
    lax: "Lax",
    strict: "Strict",
  }[cookie.sameSite];

  return {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path,
    expires: cookie.session ? -1 : cookie.expirationDate,
    httpOnly: cookie.httpOnly,
    secure: cookie.secure,
    ...(sameSite ? { sameSite } : {}),
  };
}

document.getElementById("export").addEventListener("click", async () => {
  status.textContent = "Читаю сессию...";

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const activeUrl = new URL(tab.url);
    if (activeUrl.hostname !== "dzen.ru" && !activeUrl.hostname.endsWith(".dzen.ru")) {
      throw new Error("Сначала откройте авторизованную вкладку Дзена.");
    }

    const [pageState] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        origin: location.origin,
        localStorage: Object.keys(localStorage).map((name) => ({
          name,
          value: localStorage.getItem(name),
        })),
      }),
    });

    const browserCookies = await chrome.cookies.getAll({});
    const cookies = browserCookies
      .filter((cookie) => isAllowedDomain(cookie.domain))
      .map(toPlaywrightCookie);
    const state = {
      cookies,
      origins: [
        {
          origin: pageState.result.origin,
          localStorage: pageState.result.localStorage,
        },
      ],
    };

    const blob = new Blob([JSON.stringify(state, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "storage_state.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = `Сохранено cookies: ${cookies.length}. Найдите storage_state.json в загрузках.`;
  } catch (error) {
    status.textContent = error.message || "Не удалось сохранить сессию.";
  }
});
