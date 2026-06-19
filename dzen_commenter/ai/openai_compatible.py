import httpx


class OpenAICompatibleProvider:
    """Адаптер для любого OpenAI-совместимого endpoint (DeepSeek/ChatGPT/...).

    Различие провайдеров задаётся через base_url/model/api_key, а не через код.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client if client is not None else httpx.Client()

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        response = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
