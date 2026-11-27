# Golden dataset inicial

`golden.jsonl` traz um conjunto pequeno de perguntas reais dos colaboradores, com o comportamento esperado do assistente. Um item por linha:

| Campo | Significado |
|---|---|
| `id` | identificador estável do item |
| `token` | o token com que a pergunta é feita pela borda (define empresa e colaborador) |
| `question` | a pergunta |
| `expected_sources` | os `document_id` que a resposta deve citar, exatamente (lista vazia quando a resposta não vem de documento) |
| `required_terms` | trechos que precisam aparecer no texto da resposta |

Os valores esperados das perguntas pessoais (saldo de férias, benefícios) correspondem ao estado inicial do `hr-fake`.

Na aplicação recebida, rodados nesta ordem e logo depois de subir com o banco vazio (`docker compose down -v` antes de subir), todos os itens passam.

# Suíte de avaliação

A suíte roda contra o ambiente do compose, sempre pela borda (`localhost:8000`), com avaliadores determinísticos em código (só a biblioteca padrão do Python 3.12, sem chave paga nem serviço externo).

```
python evals/suite.py
```

| Arquivo | Conteúdo |
|---|---|
| `golden.jsonl` | o golden original (formato acima), roda primeiro e na ordem do arquivo |
| `incidents.jsonl` | itens de regressão de cada incidente, com `"incident": "INC-0N"` |
| `attacks.jsonl` | baseline de ataques do INC-05 (`"incident": "INC-05"`, `"attack"` e `"attack_success"`) |
| `checks.jsonl` | verificações de não regressão, sem campo de incidente |
| `suite.py` | o executor e os avaliadores |
| `baselines/v1-baseline.json` | o relatório da aplicação recebida, gerado na tag `v1-baseline` |

Itens com `steps` são sequências: `ask` (pergunta pela borda, com `expect`), `publish`/`delete`/`restore_kb` (documentos pelo token do RH), `hr_patch` e `reset` (simulados), `mark_calls`/`check_calls` (`/admin/calls`) e `jaeger` (API do Jaeger). Cada item termina em `PASS`, `FAIL` ou `ERROR`; `ERROR` é avaliador que não conseguiu executar (serviço fora do ar, resposta HTTP inesperada, trace que não chegou) e nunca conta como aprovado. A suíte assume um ambiente recém-subido com `docker compose down -v` e restaura os documentos que altera.

Cada execução grava `evals/reports/report-<timestamp>.json` e `evals/reports/latest.json` (fora do git) com o resultado de cada item: `id`, `incident`, `attack`, `status`, `reasons` e os passos executados (`origin`, resposta e fontes de cada pergunta).
