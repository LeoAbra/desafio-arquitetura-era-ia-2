# INC-04: o custo com IA mais que triplicou, sem nenhum erro

Relato: [`incidents/INC-04-custo-sem-erro.md`](../../incidents/INC-04-custo-sem-erro.md)

## Reprodução (aplicação recebida, ambiente limpo)

```bash
curl -s -X POST localhost:8090/admin/reset
ask tok-daniela "Qual a operadora do plano de saúde?"
ask tok-daniela "As férias coletivas são descontadas do saldo?"
curl -s "localhost:8090/admin/calls?last=10"
```

## O que foi observado

As duas perguntas chegam ao modelo (`origin: model`). Em `/admin/calls`:

| Chamada | `input_tokens` | `cached_tokens` | `cost_usd` |
|---|---|---|---|
| `answer` (1ª) | 1643 | 0 | 0.0045175 |
| `answer` (2ª) | 1645 | 0 | 0.0044625 |

Cerca de 1.440 dos ~1.645 tokens de entrada são o system prompt, igual em toda chamada, e mesmo assim nada é lido do cache do provider. Com o prefixo reaproveitado (1.408 tokens a 10% do preço), a entrada de cada chamada custaria cerca de US$ 0,0009 em vez de US$ 0,0041: a razão entre 5,43 e 1,59 do relato (×3,4) é a dessa conta.

`app/CHANGELOG.md`, 2026-08-12: "O assistente passa a receber a data e a hora atuais no início das instruções do modelo." A data do relato é exatamente a virada de custo: 01/08–11/08 US$ 1,59/dia, a partir de 12/08 US$ 5,43/dia, com o mesmo volume de chamadas.

## Causa

`app/assistant/prompt.py`: o timestamp com microssegundos é a primeira linha do system prompt.

```python
def system_prompt() -> str:
    now = datetime.now(BRT).isoformat()
    return f"Data e hora atual: {now}\n\n{MANUAL}"
```

O cache de prompt do provider é por prefixo: qualquer caractere diferente no começo da entrada zera o aproveitamento dali em diante. A entrada serializada começa com `<|system|>\nData e hora atual: 2026-…T…:…:….ffffff`, que muda a cada chamada; o prefixo comum com a chamada anterior tem ~30 caracteres, muito abaixo dos 1.024 tokens mínimos. Nada falha: só deixa de haver desconto e cada chamada processa a entrada inteira (o "um pouco mais lento" do relato, 0,4 ms por token não cacheado).

## Correção

ADR: [0003 — Estrutura do prompt para o cache do provider](../adr/0003-estrutura-do-prompt-para-o-cache-do-provider.md)

## Itens da suíte que cobrem o incidente

- `inc04-prefixo-no-cache-do-provider`
- `inc06-captura-de-conteudo-por-ambiente`, rodado em `development`, confere que a data e a hora continuam chegando ao modelo
