"""
Token 使用统计模块。
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from threading import Lock

from .types import GatewayConfig


class UsageStats:
    """Token 使用统计"""

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._stats: dict[str, list[dict]] = defaultdict(list)
        self._lock = Lock()
        self._stats_file = Path("logs/usage_stats.json")
        self._load_stats()

    def record(self, provider: str, model: str, input_tokens: int, output_tokens: int,
               latency_ms: float, success: bool, route_rule: str):
        """记录一次请求"""
        with self._lock:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "provider": provider,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "latency_ms": latency_ms,
                "success": success,
                "route_rule": route_rule,
            }
            self._stats[provider].append(entry)
            self._save_stats()

    def get_range(self, start: datetime, end: datetime) -> dict:
        """获取指定时间范围的统计"""
        result = {}

        with self._lock:
            for provider, entries in self._stats.items():
                range_entries = [
                    e for e in entries
                    if start <= datetime.fromisoformat(e["timestamp"]) < end
                ]

                if not range_entries:
                    continue

                total_input = sum(e["input_tokens"] for e in range_entries)
                total_output = sum(e["output_tokens"] for e in range_entries)
                success_count = sum(1 for e in range_entries if e["success"])
                total_latency = sum(e["latency_ms"] for e in range_entries)

                result[provider] = {
                    "request_count": len(range_entries),
                    "success_count": success_count,
                    "fail_count": len(range_entries) - success_count,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "total_tokens": total_input + total_output,
                    "avg_latency_ms": total_latency / len(range_entries),
                    "models": list(set(e["model"] for e in range_entries)),
                }

        return result

    def get_today(self) -> dict:
        """获取今天的统计"""
        now = datetime.now()
        return self.get_range(now.replace(hour=0, minute=0, second=0, microsecond=0), now)

    def get_summary(self) -> dict:
        """获取总体统计"""
        result = {}
        total_tokens = 0
        total_requests = 0

        with self._lock:
            for provider, entries in self._stats.items():
                total_provider_tokens = sum(e["total_tokens"] for e in entries)
                total_provider_requests = len(entries)
                total_tokens += total_provider_tokens
                total_requests += total_provider_requests

                result[provider] = {
                    "total_requests": total_provider_requests,
                    "total_tokens": total_provider_tokens,
                }

        return {
            "providers": result,
            "total_tokens": total_tokens,
            "total_requests": total_requests,
        }

    def _save_stats(self):
        """保存到文件"""
        try:
            self._stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._stats_file, "w", encoding="utf-8") as f:
                json.dump(dict(self._stats), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_stats(self):
        """从文件加载"""
        try:
            if self._stats_file.exists():
                with open(self._stats_file, encoding="utf-8") as f:
                    data = json.load(f)
                    self._stats = defaultdict(list, data)
        except Exception:
            pass


# 全局实例
_usage_stats: UsageStats | None = None


def get_usage_stats(config: GatewayConfig) -> UsageStats:
    global _usage_stats
    if _usage_stats is None:
        _usage_stats = UsageStats(config)
    return _usage_stats