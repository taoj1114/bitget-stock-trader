"""配置校验 — 使用 Pydantic 验证 config.yaml 结构

轻量校验：只检查关键字段的类型和存在性，不阻塞启动。
"""

from typing import Optional

from pydantic import BaseModel, Field


class BitgetConfig(BaseModel):
    base_url: str = "https://api.bitget.com"
    rate_limit: int = 20


class EastmoneyConfig(BaseModel):
    mode: str = "auto"  # auto | direct | proxy


class DatasourcesConfig(BaseModel):
    bitget: BitgetConfig = Field(default_factory=BitgetConfig)
    eastmoney: EastmoneyConfig = Field(default_factory=EastmoneyConfig)


class SearXNGConfig(BaseModel):
    base_url: str = "http://localhost:8080"
    max_results: int = 15
    timeout: int = 10


class NewsSourcesConfig(BaseModel):
    primary: str = "searxng"
    fallback: Optional[str] = None
    searxng: SearXNGConfig = Field(default_factory=SearXNGConfig)


class CacheConfig(BaseModel):
    memory_ttl: int = 300
    kline_ttl: int = 86400


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class AppConfig(BaseModel):
    """应用配置 schema"""
    mode: str = "real"
    symbols: list[str] = Field(default_factory=list)
    datasources: DatasourcesConfig = Field(default_factory=DatasourcesConfig)
    news_sources: NewsSourcesConfig = Field(default_factory=NewsSourcesConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


def validate_config(data: dict) -> AppConfig:
    """校验配置字典，返回类型化配置对象。

    Raises:
        pydantic.ValidationError: 配置不合规
    """
    return AppConfig(**data)


def validate_config_lenient(data: dict) -> tuple[AppConfig, list[str]]:
    """宽松校验：返回配置对象 + 警告列表，不抛异常。"""
    warnings = []
    try:
        cfg = AppConfig(**data)
    except Exception as e:
        # 尝试只解析核心字段
        warnings.append(f"配置格式有误，使用默认值: {e}")
        cfg = AppConfig()
    return cfg, warnings
