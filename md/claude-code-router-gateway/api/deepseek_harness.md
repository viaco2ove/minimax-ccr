``` yaml
llm-pi-ai:
  providers:
    {
      orcg:
        {
          displayName: orcg,
          apiKeyEnv: ORCG_API_KEY,
          api: openai-completions,
          baseURL: http://127.0.0.1:3428/v1,
          models: [ { id: orcg, name: orcg, contextWindow: 556000, maxTokens: 6400 } ]
        }
    }
```
主要是maxTokens 不要太大！