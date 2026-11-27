# 0003. Manter o prefixo do prompt estável: conteúdo fixo primeiro, data e hora no fim

- Status: aceita
- Data: 2026-11-27
- Nível: software
- Incidente: [INC-04](../incidents/INC-04.md)

## Contexto

Desde 12/08 (CHANGELOG: data e hora "no início das instruções do modelo") o custo por dia foi de US$ 1,59 para US$ 5,43 com o mesmo volume de chamadas. Em `/admin/calls`, duas perguntas seguidas que chegam ao modelo têm `cached_tokens = 0`, embora ~1.440 dos ~1.645 tokens de entrada sejam o mesmo manual em toda chamada.

O cache de prompt do provider é por prefixo: só conta a partir de 1.024 tokens em comum com uma chamada dos últimos 5 minutos, em blocos de 128, e qualquer caractere diferente zera o aproveitamento dali em diante. Com `Data e hora atual: <isoformat com microssegundos>` na primeira linha, o prefixo comum entre duas chamadas era de ~30 caracteres.

A feature de 12/08 é requisito: o modelo continua recebendo a data e a hora atuais em toda chamada.

## Opções consideradas

1. Tirar a data do prompt. Recupera o custo e quebra a feature de 12/08; proibido.
2. Arredondar a data (por dia ou hora) e mantê-la no início. Reduz a quebra a uma por hora/dia, mas perde a hora atual e continua frágil a qualquer outro dado variável que entre no começo.
3. **Ordenar o prompt do mais estável para o mais variável: manual fixo primeiro, data e hora no fim do system prompt, e na mensagem do usuário a pergunta e o contexto.**

## Decisão

Opção 3, em `app/assistant/prompt.py` (`system_prompt`): `f"{MANUAL}\nData e hora atual: {now}"` com `now` em segundos no fuso de Brasília. A regra de estrutura vale para qualquer mudança futura no prompt: nada que varie por chamada (data, empresa, colaborador, pergunta, contexto) entra antes do conteúdo fixo.

Conta (preços do provider: entrada 2,50, entrada do cache 0,25, saída 10,00 US$/1M tokens), para uma chamada típica com 1.638 tokens de entrada e 32 de saída:

- antes: 1.638 × 2,50 + 32 × 10 = US$ 0,00442
- depois: o prefixo comum é o manual inteiro (~1.450 tokens) → 1.408 tokens (11 blocos de 128) lidos do cache; 230 × 2,50 + 1.408 × 0,25 + 32 × 10 = US$ 0,00125

Redução de ~3,5× no custo por chamada, a razão do relato (5,43 / 1,59 = 3,4). A latência também cai: o provider soma 0,4 ms por token de entrada não cacheado (~0,56 s a menos por chamada).

## Consequências

- Melhor: custo e latência das chamadas ao modelo voltam ao patamar de antes de 12/08, sem perder a data e a hora.
- Pior: o aproveitamento depende de o manual ter mais de 1.024 tokens e de as chamadas acontecerem a menos de 5 minutos umas das outras; um manual muito mais curto ou tráfego esparso deixam de aproveitar o cache, sem erro nenhum.
- Vigiar: `gen_ai.usage.cache_read.input_tokens` no span da chamada ao modelo ([ADR 0005](0005-politica-de-telemetria.md)) e o item da suíte abaixo, que roda no gate. É esse cache que "quebra em silêncio"; agora uma regressão reprova o PR.

## Evidência

Item da suíte: `inc04-prefixo-no-cache-do-provider` (falha na baseline com `cached_tokens=0`, passa na `main`). Em `development`, `inc06-captura-de-conteudo-por-ambiente` confere que as mensagens enviadas ao modelo trazem `Data e hora atual`.

Pela borda (roteiro do avaliador, passo 5):

```bash
curl -s -X POST localhost:8090/admin/reset
ask tok-daniela "Qual a operadora do plano de saúde?"
ask tok-daniela "As férias coletivas são descontadas do saldo?"
curl -s "localhost:8090/admin/calls?last=10"   # a chamada answer mais recente com "cached_tokens": 1408
```
