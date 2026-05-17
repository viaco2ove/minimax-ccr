"""日志控制器 - 集中管理各模块的日志级别和行为"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("ccrg")

_CONFIG_PATH = Path(__file__).parent.parent.parent / "log_config.json"


def _load_config() -> dict:
    """加载日志配置"""
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(cfg: dict):
    """保存日志配置"""
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# 默认配置（首次使用或文件损坏时）
_DEFAULT_CONFIG = {
    "TRANSLATOR_VERBOSE": False,
    "TRANSLATOR_CONVERT_CHUNKS": True,
    "TRANSLATOR_CONVERTER": False,
    "SSE_CLIENT": False,
}


def is_enabled(key: str) -> bool:
    """检查某个日志开关是否启用"""
    cfg = _load_config()
    return cfg.get(key, _DEFAULT_CONFIG.get(key, False))


def verbose_log(tag: str, msg: str, config_key: str = "TRANSLATOR_VERBOSE"):
    """根据配置决定是否打印日志"""
    if is_enabled(config_key):
        logger.debug(f"[{tag}] {msg}")