class YandexGPTProvider:
    """Заготовка адаптера YandexGPT. Не реализован в MVP."""

    def __init__(
        self, *, api_key: str = "", base_url: str = "", model: str = ""
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        raise NotImplementedError("YandexGPTProvider не реализован в MVP")
