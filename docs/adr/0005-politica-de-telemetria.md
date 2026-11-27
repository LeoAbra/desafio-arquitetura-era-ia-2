# 0005. Política de telemetria: Collector no meio, spans por etapa, captura por ambiente e redação no Collector

- Status: aceita
- Data: 2026-11-27
- Nível: corporativa
- Incidente: [INC-06](../incidents/INC-06.md)

## Contexto

A auditoria do DPO achou CPF e salário nos traces de produção do Jaeger, acessível a todo o time e guardado por semanas. A aplicação gravava pergunta, prompt (com o bloco do `hr-fake`: nome, CPF, salário, saldo e benefícios) e resposta num único span `ask`, em qualquer ambiente, e exportava direto para o Jaeger. O suporte, por outro lado, precisa ver o que o assistente recebeu para investigar reclamações. E um span só por requisição não mostra em que etapa (cache, busca, contexto, chamada) a falha aconteceu — o que foi decisivo nos INC-01 a INC-04.

Uma política de telemetria vale para qualquer sistema com IA da Vereda, não só para o assistente: por isso o nível corporativo.

## Opções consideradas

1. Não capturar conteúdo nunca. Atende o DPO e deixa o suporte cego.
2. Redigir só na aplicação. Protege o que a aplicação sabe capturar hoje, não o que alguém capturar por engano amanhã, e cada serviço precisa reimplementar.
3. Redigir só no Collector e capturar tudo em todo ambiente. Uma regex que falhe expõe produção inteira.
4. **Aplicação decide o que capturar por ambiente (uma configuração só); Collector como único caminho até o backend e última barreira, redigindo CPF e salário em qualquer ambiente.**

## Decisão

Opção 4.

- **Caminho**: `otel-collector` (imagem `otel/opentelemetry-collector-contrib`, configuração em `otel/collector.yaml`) recebe OTLP da aplicação (`OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318`) e é o único que exporta para o Jaeger. O destino passa a ser uma escolha reversível no Collector.
- **Spans por etapa** (serviço `vereda-assistant`): `ask` (raiz: `app.tenant_id`, `app.employee_id`, `app.origin`, `app.cache.eligible`) → `cache.exact.lookup` → `embeddings text-embedding-fake` → `cache.semantic.lookup` → `hr.get_employee` (só pessoal) → `retrieval.search` (`app.retrieval.doc_ids`) → `prompt.build` (`app.guard.context_removed`) → `chat gpt-fake-large` → `guard.output` → `cache.store`. O span da chamada ao modelo segue as GenAI semantic conventions: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`.
- **Captura de conteúdo**: decidida só por `APP_ENV` (`config.CAPTURE_CONTENT`). Em `production` (padrão), nenhum conteúdo: nem pergunta, nem mensagens, nem resposta. Em `development`, o span da chamada traz `gen_ai.input.messages` (system com data e hora, e user com pergunta e contexto) e `gen_ai.output.messages`, e o raiz traz `app.question` e `app.answer`. No compose, `APP_ENV: ${APP_ENV:-production}`: `APP_ENV=development docker compose up -d --wait app` sobe em development; `docker compose up -d --wait app` volta para production.
- **Redação** no Collector (`transform/redact_pii`), em atributos de span e de evento, em qualquer ambiente: CPF (`\d{3}\.\d{3}\.\d{3}-\d{2}` → `[CPF REDIGIDO]`), valor que segue `salário`/`salary` e qualquer valor monetário com separador de milhar (`R$ 4.850,00` → `[VALOR REDIGIDO]`).

A aplicação decide o que capturar; o Collector protege inclusive do que alguém capturar por engano amanhã. As duas coisas são verdade ao mesmo tempo: o suporte vê o que o assistente recebeu em development, e CPF e salário não chegam ao Jaeger em ambiente nenhum.

## Consequências

- Melhor: cada etapa tem duração e atributos próprios (`origin`, hit de cache, documentos recuperados, tokens lidos do cache do provider, ação da defesa do INC-05), sem dado pessoal; a redação vale também para a aplicação da v1 rodando atrás do Collector.
- Pior: em development, valores com milhar que não são salário também aparecem redigidos; o suporte vê `[VALOR REDIGIDO]` no lugar.
- Residual: o Collector só redige o que reconhece. Nome do colaborador e `app.employee_id` (identificador interno, não CPF) continuam nos traces de development / em todos, respectivamente; dados pessoais em outro formato (endereço, conta) precisariam de regra nova. O conteúdo em development continua sendo dado de investigação, com acesso restrito ao suporte.
- Se o Collector cair na subida por erro de configuração, o `--wait` termina com erro (`docker compose logs otel-collector`).

## Evidência

Itens da suíte: `inc06-sem-cpf-e-salario-no-jaeger`, `inc06-captura-de-conteudo-por-ambiente`, `inc06-spans-por-etapa` (falham na baseline, passam na `main`; o primeiro também passa na rodada da v1 do gate, porque a redação está fora de `app/`).

```bash
docker compose config | grep -A3 otel-collector
curl -s "localhost:16686/api/traces?service=vereda-assistant&limit=500" | grep -oE '[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|4\.850,00|3\.200,00|7\.400,00|5\.950,00' | wc -l   # 0
curl -s "localhost:16686/api/traces?service=vereda-assistant&limit=500" | grep -o "Quantos dias de férias eu tenho" | wc -l   # 0 em production
APP_ENV=development docker compose up -d --wait app
ask tok-ana "Quantos dias de férias eu tenho?"
curl -s "localhost:16686/api/traces?service=vereda-assistant&limit=5" | grep -o 'Data e hora atual: [^\\]*'   # a data e a hora enviadas ao modelo
curl -s "localhost:16686/api/traces?service=vereda-assistant&limit=5" | grep -o 'CPF: [^\\]*'                 # CPF: [CPF REDIGIDO]
docker compose up -d --wait app   # volta para production
```
