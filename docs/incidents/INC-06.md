# INC-06: CPF e salário na telemetria

Relato: [`incidents/INC-06-dados-pessoais-na-telemetria.md`](../../incidents/INC-06-dados-pessoais-na-telemetria.md)

## Reprodução (aplicação recebida, ambiente limpo)

```bash
ask tok-ana "Quantos dias de férias eu tenho?"
sleep 3
curl -s "localhost:16686/api/traces?service=vereda-assistant&limit=500" \
  | grep -oE '[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|4\.850,00|3\.200,00|7\.400,00|5\.950,00' | sort | uniq -c
curl -s "localhost:16686/api/traces?service=vereda-assistant&limit=500" | grep -o "Quantos dias de férias eu tenho" | wc -l
```

## O que foi observado

- O grep encontra `418.273.905-61` (CPF da Ana) e `4.850,00` (salário dela); com o Bruno perguntando os benefícios, aparecem também `315.948.207-83` e `7.400,00`. A busca pelo texto da pergunta encontra ocorrências, com `APP_ENV=production`.
- No Jaeger, o trace tem um único span, `ask`, com os atributos:
  - `app.question`: a pergunta
  - `app.prompt`: a mensagem inteira enviada ao modelo, com o bloco `[source: colaborador]` → `Nome: Ana Souza`, `CPF: 418.273.905-61`, `Salário: R$ 4.850,00`, saldo e benefícios
  - `app.answer`: a resposta
  - `app.tenant_id`, `app.employee_id`, `app.origin`, `app.total_tokens`
- Não há span por etapa: não dá para ver se a resposta veio do cache, da busca ou do modelo, nem quanto custou cada chamada.
- A aplicação exporta direto para o Jaeger (`OTEL_EXPORTER_OTLP_ENDPOINT: http://jaeger:4318` no `compose.yaml`): não há nenhum ponto entre a aplicação e o backend onde se possa decidir o que sai.

## Causa

- `app/assistant/main.py`, `ask`: o conteúdo é gravado no span sem considerar o ambiente.

```python
span.set_attribute("app.question", question)
...
span.set_attribute("app.prompt", messages[1]["content"])   # inclui CPF e salário do hr-fake
span.set_attribute("app.answer", answer)
```

- `app/assistant/prompt.py`, `employee_block`: o contexto enviado ao modelo inclui CPF e salário; por isso o prompt capturado os carrega.
- `app/assistant/telemetry.py` + `compose.yaml`: exportação direta ao Jaeger, sem Collector e sem redação. `config.APP_ENV` existe, mas nada o consulta para decidir a captura (CHANGELOG 2026-05-15: "Traces enviados ao Jaeger com a pergunta, o prompt e a resposta").

## Correção

ADR: [0005 — Política de telemetria: captura por ambiente e redação no Collector](../adr/0005-politica-de-telemetria.md)

## Itens da suíte que cobrem o incidente

- `inc06-sem-cpf-e-salario-no-jaeger`
- `inc06-captura-de-conteudo-por-ambiente`
- `inc06-spans-por-etapa`
