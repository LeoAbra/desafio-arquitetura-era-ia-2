# Vereda RH: do incidente à arquitetura confiável

Entrega do desafio do módulo Arquitetura na Era da IA (MBA FullCycle). O enunciado está no [repositório base](https://github.com/devfullcycle/desafio-arquitetura-era-ia-2).

## Visão geral

O assistente de RH da Vereda (Python 3.12 + FastAPI, RAG no pgvector, cache exato e semântico) tinha seis incidentes abertos, todos respondidos com `200`. Cada um foi reproduzido na aplicação recebida e diagnosticado no código ([`docs/incidents/`](docs/incidents/)), virou item de uma suíte de avaliação determinística que roda pela borda ([`evals/`](evals/)) e foi registrado numa baseline da aplicação recebida (tag [`v1-baseline`](https://github.com/LeoAbra/desafio-arquitetura-era-ia-2/tree/v1-baseline), `evals/baselines/v1-baseline.json`), onde todos os itens de incidente e de ataque falham.

As correções ficaram na arquitetura em volta das chamadas de IA, sem reescrever a aplicação: a chave do cache passou a conter a empresa e respostas pessoais deixaram de ser cacheadas; a reindexação trata a remoção e invalida as respostas derivadas da base; o prompt mantém o prefixo estável para o cache do provider; o conteúdo publicado pelo RH é tratado como dado na montagem do contexto e na saída; e a telemetria passa por um OpenTelemetry Collector, com um span por etapa, conteúdo só em `development` e redação de CPF e salário no Collector. Um gate pareado compara cada item com a baseline e roda localmente e no GitHub Actions. As decisões estão em ADRs ([`docs/adr/`](docs/adr/)) e no [threat model](docs/threat-model.md).

## Como rodar

Pré-requisitos: Docker com Compose v2, Git e Python 3.12 (só para a suíte e o gate, sem dependências além da biblioteca padrão). Portas usadas no host: `8000`, `8090`, `8091`, `5432` e `16686`.

### Subir em `production` (padrão)

```bash
docker compose down -v && cp .env.example .env && docker compose up -d --build --wait
curl -s localhost:8000/health
```

`APP_ENV` vem de `${APP_ENV:-production}` no `compose.yaml`: sem nada definido, a aplicação sobe em `production` e nenhum conteúdo (pergunta, mensagens, resposta) vai para os traces.

### Subir em `development` (captura das mensagens enviadas ao modelo)

```bash
APP_ENV=development docker compose up -d --wait app      # bash
docker compose exec app printenv APP_ENV                   # development
```

```powershell
$env:APP_ENV="development"; docker compose up -d --wait app; Remove-Item Env:APP_ENV
```

Para voltar a `production`, recrie o serviço sem a variável:

```bash
docker compose up -d --wait app
docker compose exec app printenv APP_ENV                   # production
```

Não coloque `APP_ENV` no `.env`: o compose o leria para a interpolação e todas as subidas seguintes ficariam em `development`.

### Suíte de avaliação

Sempre a partir de um ambiente recém-subido (`docker compose down -v` antes):

```bash
python evals/suite.py
```

Grava `evals/reports/report-<timestamp>.json` e `evals/reports/latest.json`. Detalhes do formato dos itens em [`evals/README.md`](evals/README.md).

### Gate

```bash
docker compose down -v && docker compose up -d --build --wait
python evals/gate.py
```

Roda a suíte e compara cada item com `evals/baselines/v1-baseline.json`. Código de saída `0` aprovado, `1` reprovado, `2` gate sem baseline. Para comparar um relatório já gerado sem rodar a suíte de novo: `python evals/gate.py --report evals/reports/latest.json`.

## Incidentes

| Incidente | Resumo |
|---|---|
| [INC-01](docs/incidents/INC-01.md) | A chave dos dois caches era só a pergunta: a resposta da Estrela era servida à Boreal (e vice-versa). |
| [INC-02](docs/incidents/INC-02.md) | A resposta com os dados do colaborador ia para o cache: o Carlos recebia o saldo da Ana, e saldo corrigido no `hr-fake` nunca aparecia. |
| [INC-03](docs/incidents/INC-03.md) | `reindex` não removia os trechos de documento apagado, e nada invalidava as respostas guardadas: o auxílio revogado continuava sendo oferecido. |
| [INC-04](docs/incidents/INC-04.md) | A data e a hora no início do system prompt quebravam o prefixo do cache do provider desde 12/08: `cached_tokens` zero e custo ×3,4. |
| [INC-05](docs/incidents/INC-05.md) | Um comentário HTML num guia publicado pelo RH virou instrução para o modelo, que mandou enviar CPF e senha para um e-mail externo. |
| [INC-06](docs/incidents/INC-06.md) | Pergunta, prompt (com CPF e salário do `hr-fake`) e resposta iam para o Jaeger em produção, num único span, sem Collector. |

## Observabilidade

A aplicação exporta OTLP/HTTP para o serviço `otel-collector` (configuração em [`otel/collector.yaml`](otel/collector.yaml)); só o Collector exporta para o Jaeger (`http://localhost:16686`, serviço `vereda-assistant`).

Spans de um `/ask` que chega ao modelo:

| Span | Etapa | Atributos |
|---|---|---|
| `ask` (raiz) | requisição | `app.tenant_id`, `app.employee_id`, `app.origin` (`exact_cache` / `semantic_cache` / `model`), `app.cache.eligible`; em development `app.question`, `app.answer` |
| `cache.exact.lookup` | cache exato | `app.cache.hit` |
| `embeddings text-embedding-fake` | embedding da pergunta | `gen_ai.operation.name=embeddings`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` |
| `cache.semantic.lookup` | cache semântico | `app.cache.hit`, `app.cache.similarity` |
| `hr.get_employee` | dados do colaborador (só pergunta pessoal) | `app.hr.found` |
| `retrieval.search` | busca de trechos | `app.retrieval.top_k`, `app.retrieval.doc_ids` |
| `prompt.build` | montagem do contexto | `app.guard.context_removed` (trechos hostis removidos) |
| `chat gpt-fake-large` | chamada ao modelo | `gen_ai.operation.name=chat`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`; em development `gen_ai.input.messages` (system com data e hora, user com pergunta e contexto) e `gen_ai.output.messages` |
| `guard.output` | defesa na saída | `app.guard.blocked`, `app.guard.reason` |
| `cache.store` | gravação nos caches | — |

Respostas vindas do cache param no span de cache correspondente. Pergunta pessoal não tem spans de cache nem `cache.store`.

**Política de captura de conteúdo** ([ADR 0005](docs/adr/0005-politica-de-telemetria.md)): uma configuração só, `APP_ENV`. Em `production`, nenhum conteúdo vai para o trace; em `development`, vão as mensagens enviadas ao modelo, a pergunta e a resposta, para o suporte investigar. O recurso do trace traz `deployment.environment.name` e `app.content_capture`.

**Redação**: no Collector, processor `transform/redact_pii`, em atributos de span e de evento e em qualquer ambiente — CPF (`[CPF REDIGIDO]`), valor que segue `salário`/`salary` e qualquer valor monetário com separador de milhar (`[VALOR REDIGIDO]`). A aplicação decide o que capturar; o Collector é a última barreira antes do backend, e vale até para a aplicação da `v1-baseline` rodando atrás dele.

```bash
curl -s "localhost:16686/api/traces?service=vereda-assistant&limit=500" | grep -oE '[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|4\.850,00|3\.200,00|7\.400,00|5\.950,00' | wc -l   # 0
```

## Gate e CI

Regras ([ADR 0006](docs/adr/0006-regras-do-gate.md)). `python evals/gate.py` roda a suíte e compara cada item com o mesmo item em `evals/baselines/v1-baseline.json`. Reprova (saída `1`) quando:

1. um avaliador não conseguiu executar (`ERROR`: serviço fora do ar, HTTP inesperado, trace que não chega) — avaliador quebrado nunca conta como aprovado;
2. um item de incidente ou um ataque falha agora;
3. um item que passava na baseline falha agora;
4. um item novo (sem estado na baseline) falha agora;
5. um item da baseline sumiu da suíte.

A baseline não é regenerada na `main`: suíte e gate só escrevem em `evals/reports/` (fora do git).

Saída (trecho da rodada contra o `app/` da `v1-baseline`):

```
PASS estrela-ferias-antecedencia                           baseline: PASS  agora: PASS
FAIL inc01-vr-boreal-depois-da-estrela (INC-01)            baseline: FAIL  agora: FAIL
     motivo: item de incidente falhando
       - a resposta contém "R$ 35,00" (resposta: "O valor do vale-refeição da Padaria Estrela é de R$ 35,00 por dia útil trabalhado.")
       - a resposta cita o documento "vale-refeicao"
...
gate: REPROVADO (18 itens: 12 item de incidente falhando, 6 ataque bem-sucedido)
```

Formato do relatório (JSON, um por execução):

```json
{
  "suite": "vereda-assistant", "started_at": "...", "finished_at": "...", "git_sha": "...",
  "summary": {"PASS": 35, "FAIL": 0, "ERROR": 0},
  "items": [
    {"id": "inc01-vr-boreal-depois-da-estrela", "source": "incidents.jsonl", "incident": "INC-01", "attack": null,
     "description": "...", "status": "PASS", "reasons": [],
     "steps": [{"action": "ask", "token": "tok-ana", "question": "...", "status": 200,
                "origin": "model", "answer": "...", "sources": ["vale-refeicao"]}],
     "duration_ms": 812}
  ]
}
```

Workflow: [`.github/workflows/gate.yml`](.github/workflows/gate.yml) sobe o compose e roda o gate em todo push e pull request, e guarda o relatório como artefato `gate-report`.

- Execução aprovada na `main`: https://github.com/LeoAbra/desafio-arquitetura-era-ia-2/actions/runs/46084329017
- Execução reprovada por uma correção desfeita de propósito ([PR #1](https://github.com/LeoAbra/desafio-arquitetura-era-ia-2/pull/1), que tira a empresa da chave do cache e desfaz a correção do INC-01): https://github.com/LeoAbra/desafio-arquitetura-era-ia-2/actions/runs/46084440339 — `gate: REPROVADO (3 itens: 3 item de incidente falhando)`, listando `inc01-vr-boreal-depois-da-estrela`, `inc01-vr-boreal-mesma-frase` e `inc01-home-office-estrela-depois-da-boreal`. O PR fica aberto, sem merge.

## Mapa de decisões

| ADR | Nível | Resumo |
|---|---|---|
| [0001](docs/adr/0001-chave-e-politica-do-cache-de-respostas.md) | solução | Empresa na chave dos caches exato e semântico; respostas com dados do `hr-fake` não são lidas nem gravadas no cache (INC-01, INC-02). |
| [0002](docs/adr/0002-invalidacao-dos-derivados-quando-a-base-muda.md) | solução | `reindex` leva o índice ao estado publicado, inclusive remoções, e invalida os caches da empresa quando a base muda (INC-03). |
| [0003](docs/adr/0003-estrutura-do-prompt-para-o-cache-do-provider.md) | software | Conteúdo fixo primeiro, data e hora no fim do system prompt: 1.408 tokens lidos do cache do provider por chamada (INC-04). |
| [0004](docs/adr/0004-defesa-contra-conteudo-hostil-na-base.md) | solução | Defesa fora do modelo, na montagem do contexto e na saída, com política de conteúdo pelo efeito e não pela forma (INC-05). |
| [0005](docs/adr/0005-politica-de-telemetria.md) | corporativa | Collector como único caminho, spans por etapa com GenAI semconv, captura por `APP_ENV` e redação de CPF/salário no Collector (INC-06). |
| [0006](docs/adr/0006-regras-do-gate.md) | corporativa | Gate pareado item a item com a baseline v1; erro de avaliador reprova; roda local e no GitHub Actions. |

Nenhum serviço além do `otel-collector` foi adicionado ao compose.

## Arquivos da solução

| Caminho | O que é |
|---|---|
| `app/assistant/` | a aplicação evoluída; `guard.py` é o único módulo novo (defesa do INC-05) |
| `otel/collector.yaml` | configuração do Collector |
| `compose.yaml` | serviço `otel-collector`, `APP_ENV` por variável, aplicação exportando para o Collector |
| `evals/suite.py`, `evals/gate.py` | suíte e gate |
| `evals/golden.jsonl`, `incidents.jsonl`, `attacks.jsonl`, `checks.jsonl` | itens da suíte |
| `evals/baselines/v1-baseline.json` | baseline gerada na tag `v1-baseline` |
| `docs/incidents/`, `docs/adr/`, `docs/threat-model.md` | diagnóstico, decisões e threat model |
| `.gitattributes` | força LF no checkout: no Windows, CRLF nos `.md` da base quebrava o front matter dentro do container |
