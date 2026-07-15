import time
import uuid

import httpx


class GigaChatProvider:
    """GigaChat REST adapter with OAuth access-token caching."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        scope: str = "GIGACHAT_API_B2B",
        oauth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        verify_ssl_certs: bool | str = True,
        token_refresh_margin: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.scope = scope
        self.oauth_url = oauth_url
        self.token_refresh_margin = token_refresh_margin
        self._client = client if client is not None else httpx.Client(verify=verify_ssl_certs)
        self._access_token = ""
        self._token_expires_at = 0.0

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        if (
            not force_refresh
            and self._access_token
            and now < self._token_expires_at - self.token_refresh_margin
        ):
            return self._access_token

        response = self._client.post(
            self.oauth_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "RqUID": str(uuid.uuid4()),
                "Authorization": f"Basic {self.api_key}",
            },
            data={"scope": self.scope},
        )
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")
        expires_at = data.get("expires_at")
        if not access_token or not isinstance(expires_at, (int, float)):
            raise ValueError("GigaChat OAuth response does not contain a valid token")

        self._access_token = access_token
        self._token_expires_at = float(expires_at)
        return access_token

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        response = self._complete(
            prompt, temperature=temperature, max_tokens=max_tokens
        )
        if response.status_code == 401:
            response = self._complete(
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                force_token_refresh=True,
            )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _complete(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        force_token_refresh: bool = False,
    ) -> httpx.Response:
        token = self._get_access_token(force_refresh=force_token_refresh)
        return self._client.post(
            f"{self.base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
