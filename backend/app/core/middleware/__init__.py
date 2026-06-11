"""FastAPI ASGI 中间件。

注册顺序（main.py app.add_middleware 调用顺序与执行顺序相反，详见 nfr-design 1.1）：
    CORS → SentryAsgi → RequestId → slowapi Limiter → TenancyContext → Auth Dep → Router
"""
