from playwright.async_api import async_playwright
import re
from core.utils import logger
import json
from types import SimpleNamespace

class FakeResponse:
    """
    Класс для эмуляции объекта response и поддержки работы как словарь.
    """
    def __init__(self, status_code, content):
        self.status_code = status_code
        self._content = content
        try:
            self._json = json.loads(content)
        except json.JSONDecodeError:
            self._json = {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        """
        Возвращает JSON-данные из ответа.
        """
        return self._json

    @property
    def text(self):
        """
        Возвращает текст ответа.
        """
        return self._content

    # Реализация методов, как у словаря
    def get(self, key, default=None):
        """
        Возвращает значение по ключу из JSON, как у словаря.
        """
        return self._json.get(key, default)

    def __getitem__(self, key):
        """
        Позволяет обращаться к JSON-данным через индексацию.
        """
        return self._json[key]

    def __contains__(self, key):
        """
        Проверяет наличие ключа в JSON-данных.
        """
        return key in self._json

    def keys(self):
        """
        Возвращает ключи JSON-объекта.
        """
        return self._json.keys()

    def values(self):
        """
        Возвращает значения JSON-объекта.
        """
        return self._json.values()

    def items(self):
        """
        Возвращает пары (ключ, значение) из JSON-объекта.
        """
        return self._json.items()

class ResponseParser:
    """
    Отвечает за обработку контента страницы и создание FakeResponse.
    """
    @staticmethod
    def parse_page_content(content):
        """
        Извлекает JSON-данные из HTML-контента страницы.
        :param content: HTML-строка страницы.
        :return: Объект FakeResponse.
        """
        try:
            # Попытка извлечь JSON из HTML (обёрнутого в <pre>)
            json_data = json.loads(content.split("<pre>")[1].split("</pre>")[0])
            return FakeResponse(status_code=200, content=json.dumps(json_data))
        except (IndexError, json.JSONDecodeError):
            raise ValueError("Failed to extract JSON from the page content")

class BaseClient:
    def __init__(self):
        self.browser = None
        self.page = None
        self.context = None
        self.default_headers = None
        self.proxy = None

    async def create_session(self, proxy=None, user_agent=None):
        """
        Создаёт сессию браузера Playwright с поддержкой прокси и эмуляции окружения.
        :param proxy: Прокси-сервер в формате:
            - "http://username:password@proxy_address:proxy_port"
            - "https://proxy_address:proxy_port"
            - "socks5://username:password@proxy_address:proxy_port".
        :param user_agent: Пользовательский User-Agent.
        """
        self.proxy = proxy
        self.default_headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "chrome-extension://lgmpfmgeabnnlemejacfljbmonaomfmm",
            "priority": "u=1, i",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "none",
            "user-agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        }

        # Проверка типа заголовков
        if not isinstance(self.default_headers, dict):
            raise TypeError(f"Headers must be a dictionary, got {type(self.default_headers).__name__}")

        playwright = await async_playwright().start()

        # Настройка прокси
        browser_args = {}
        if proxy:
            browser_args["proxy"] = self._parse_proxy(proxy)

        # Запуск браузера
        self.browser = await playwright.chromium.launch(
            headless=True,  # Установите False для отладки
            **browser_args,
        )

        # Создание контекста с настройками устройства
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},  # Эмуляция экрана
            user_agent=self.default_headers["user-agent"],  # Пользовательский User-Agent
            locale="en-US",  # Локаль браузера
            device_scale_factor=1.0,  # Масштаб устройства
            color_scheme="light",  # Цветовая схема
        )
        self.page = await self.context.new_page()

        # Установка заголовков
        await self.page.set_extra_http_headers(self.default_headers)

        # Удаление признака автоматизации
        await self.page.evaluate(
            "() => { Object.defineProperty(navigator, 'webdriver', { get: () => false }); }"
        )

    async def close_session(self):
        """
        Завершает сессию браузера.
        """
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.page = None
            self.context = None

    async def make_request(self, method, url, headers=None, json_data=None):
        """
        Выполняет запрос с эмуляцией браузера.
        :param method: HTTP-метод (GET или POST).
        :param url: Целевой URL.
        :param headers: Дополнительные заголовки.
        :param json_data: Данные для POST-запроса.
        :return: Ответ в виде HTML или JSON.
        """
        if not self.page:
            raise Exception("Session not initialized. Call create_session first.")

        if headers:
            await self.page.set_extra_http_headers(headers)

        try:
            if method.upper() == "GET":
                await self.page.goto(url, wait_until="domcontentloaded")
                await self.page.wait_for_load_state("networkidle")  # Ожидание завершения загрузки
                # Возвращаем данные страницы
                # return await self.page.content()

            elif method.upper() == "POST":
                await self.page.goto(url, wait_until="domcontentloaded")
                await self.page.evaluate(
                    """(json_data) => {
                        const form = document.createElement('form');
                        form.method = 'POST';
                        form.action = location.href;
                        for (const key in json_data) {
                            const input = document.createElement('input');
                            input.name = key;
                            input.value = json_data[key];
                            form.appendChild(input);
                        }
                        document.body.appendChild(form);
                        form.submit();
                    }""",
                    json_data,
                )
                await self.page.wait_for_load_state("networkidle")  # Ожидание завершения загрузки
            else:
                raise ValueError("Unsupported HTTP method")

            logger.info(await self.page.content())
            # Получение HTML-контента страницы
            content = await self.page.content()

            logger.info(content)
            # Используем ResponseParser для создания FakeResponse
            return ResponseParser.parse_page_content(content)


        except Exception as e:
            raise Exception(f"Request failed: {str(e)}")

    async def __aenter__(self):
        """
        Контекстный менеджер для автоматического создания сессии.
        """
        await self.create_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Контекстный менеджер для автоматического завершения сессии.
        """
        await self.close_session()

    def _parse_proxy(self, proxy):
        """
        Парсинг строки прокси и подготовка параметров для Playwright.
        :param proxy: Прокси-сервер в формате:
            - "http://username:password@proxy_address:proxy_port"
            - "https://proxy_address:proxy_port"
            - "socks5://username:password@proxy_address:proxy_port".
        :return: Словарь с параметрами для Playwright.
        """
        proxy_pattern = re.compile(
            r'^(?P<scheme>http|https|socks5)://(?:(?P<username>[^:]+):(?P<password>[^@]+)@)?(?P<address>[^:]+):(?P<port>\d+)$'
        )
        match = proxy_pattern.match(proxy)
        if not match:
            raise ValueError(f"Invalid proxy format: {proxy}")

        groups = match.groupdict()
        proxy_config = {
            "server": f"{groups['scheme']}://{groups['address']}:{groups['port']}"
        }
        if groups["username"] and groups["password"]:
            proxy_config["username"] = groups["username"]
            proxy_config["password"] = groups["password"]

        return proxy_config
