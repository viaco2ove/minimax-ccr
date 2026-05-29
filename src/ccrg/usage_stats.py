"""
Token 使用统计模块 (SQLite 后端)。
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from threading import Lock

from .types import GatewayConfig

logger = logging.getLogger("ccrg")


class UsageStats:
    """Token 使用统计 (SQLite)"""

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._lock = Lock()
        self._db_file = Path("logs/usage_stats.db")
        self._db_file.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（线程安全）"""
        conn = sqlite3.connect(str(self._db_file), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        """初始化数据库表"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    latency_ms REAL DEFAULT 0,
                    success INTEGER DEFAULT 1,
                    route_rule TEXT DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON usage_records(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_provider ON usage_records(provider)")

    def record(self, provider: str, model: str, input_tokens: int, output_tokens: int,
               latency_ms: float, success: bool, route_rule: str):
        """记录一次请求"""
        total_tokens = input_tokens + output_tokens
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    conn.execute("""
                        INSERT INTO usage_records 
                        (timestamp, provider, model, input_tokens, output_tokens, total_tokens, latency_ms, success, route_rule)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        datetime.now().isoformat(),
                        provider, model, input_tokens, output_tokens, total_tokens,
                        latency_ms, 1 if success else 0, route_rule
                    ))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Failed to record usage stats: {e}")

    def get_range(self, start: datetime, end: datetime) -> dict:
        """获取指定时间范围的统计"""
        result = {}
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.execute("""
                        SELECT 
                            provider,
                            COUNT(*) as request_count,
                            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
                            SUM(input_tokens) as total_input,
                            SUM(output_tokens) as total_output,
                            SUM(total_tokens) as total_tokens,
                            AVG(latency_ms) as avg_latency,
                            GROUP_CONCAT(DISTINCT model) as models
                        FROM usage_records
                        WHERE timestamp >= ? AND timestamp < ?
                        GROUP BY provider
                    """, (start.isoformat(), end.isoformat()))

                    for row in cursor.fetchall():
                        provider, req_count, success_count, total_input, total_output, total_tokens, avg_latency, models = row
                        result[provider] = {
                            "request_count": req_count,
                            "success_count": success_count,
                            "fail_count": req_count - success_count,
                            "input_tokens": total_input or 0,
                            "output_tokens": total_output or 0,
                            "total_tokens": total_tokens or 0,
                            "avg_latency_ms": avg_latency or 0,
                            "models": [m.strip() for m in (models or "").split(",") if m.strip()],
                        }
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Failed to get usage stats range: {e}")
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
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.execute("""
                        SELECT 
                            provider,
                            COUNT(*) as total_requests,
                            SUM(total_tokens) as total_tokens
                        FROM usage_records
                        GROUP BY provider
                    """)

                    for row in cursor.fetchall():
                        provider, req_count, provider_tokens = row
                        result[provider] = {
                            "total_requests": req_count,
                            "total_tokens": provider_tokens or 0,
                        }
                        total_tokens += provider_tokens or 0
                        total_requests += req_count
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Failed to get usage stats summary: {e}")

        return {
            "providers": result,
            "total_tokens": total_tokens,
            "total_requests": total_requests,
        }

    def cleanup(self, days: int = 30):
        """清理 N 天前的数据"""
        try:
            cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            cutoff = cutoff - timedelta(days=days)
            with self._lock:
                conn = self._get_conn()
                try:
                    conn.execute("DELETE FROM usage_records WHERE timestamp < ?", (cutoff.isoformat(),))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Failed to cleanup old usage stats: {e}")


# 全局实例
_usage_stats: UsageStats | None = None


def get_usage_stats(config: GatewayConfig) -> UsageStats:
    global _usage_stats
    if _usage_stats is None:
        _usage_stats = UsageStats(config)
    return _usage_stats
