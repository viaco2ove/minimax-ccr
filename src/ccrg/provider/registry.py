"""
Provider 注册表。
"""

import logging
from typing import Iterator

from ..types import GatewayConfig, ProviderConfig

logger = logging.getLogger("ccrg.provider")


class ProviderRegistry:
    """Provider 注册表，提供 provider 查找能力"""

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._providers: dict[str, ProviderConfig] = config.providers

        # 建立 bare model → provider 的映射（first-registered wins）
        self._bare_model_map: dict[str, str] = {}
        for name, provider in self._providers.items():
            for model in provider.models:
                if model not in self._bare_model_map:
                    self._bare_model_map[model] = name

        logger.debug(f"Registered {len(self._providers)} providers, {len(self._bare_model_map)} bare models")

    def get(self, name: str) -> ProviderConfig | None:
        """根据名称获取 provider"""
        return self._providers.get(name)

    def get_by_model(self, provider: str, model: str) -> ProviderConfig | None:
        """根据 provider 名和模型名获取 provider"""
        p = self._providers.get(provider)
        if p and model in p.models:
            return p
        return None

    def resolve_bare_model(self, model: str) -> tuple[str, str] | None:
        """解析 bare model 名称，返回 (provider_name, model)"""
        provider = self._bare_model_map.get(model)
        if provider:
            return provider, model
        return None

    def iter_providers(self) -> Iterator[tuple[str, ProviderConfig]]:
        """迭代所有 provider"""
        return iter(self._providers.items())

    def has_capability(self, provider_name: str, capability: str) -> bool:
        """检查 provider 是否有指定能力"""
        p = self._providers.get(provider_name)
        if not p:
            return False
        return p.capabilities.get(capability, False)
