curl 打印
```
# Debug: 保存完整请求体到文件，curl 用 @file 引用（避免 truncate）
        if logger.isEnabledFor(logging.DEBUG):
            req_dir = Path("logs/req")
            req_dir.mkdir(parents=True, exist_ok=True)
            req_file = req_dir / f"{request_id}_{prov_name}.json"
            with open(req_file, "w", encoding="utf-8") as f:
                json.dump(req_for_provider, f, ensure_ascii=False)
            curl_cmd = (
                f"curl -X POST '{prov_target_url}' \\\n"
                f"  -H 'Content-Type: application/json' \\\n"
                f"  -H 'Authorization: Bearer ***' \\\n"
                f"  -H 'anthropic-version: 2023-06-01' \\\n"
                f"  -d @logs/req/{req_file.name}"
            )
            logger.debug(f"[FallbackRouter] [REQ] [CURL] [{prov_name}]: {req_file} (chars={len(json.dumps(req_for_provider, ensure_ascii=False))})\n{curl_cmd}")
```