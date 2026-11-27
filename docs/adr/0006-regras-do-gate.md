# 0006. Gate pareado item a item com a baseline v1, local e no CI

- Status: aceita
- Data: 2026-11-27
- Nível: corporativa

## Contexto

Os seis incidentes responderam `200`: nenhuma métrica tradicional os viu. Uma correção pode resolver um e reabrir outro (desligar o cache resolve INC-01/INC-02 e devolve o INC-04). "Reprovar se a média cair abaixo de 0,80" não diz o que piorou e deixa um item crítico quebrar enquanto a média sobe. E um avaliador que não roda (serviço fora do ar, trace que não chega) não pode parecer aprovação.

## Opções consideradas

1. Limiar de média (taxa de aprovação). Não identifica o item e esconde regressão crítica.
2. Exigir 100% de aprovação. Simples, mas sem baseline não distingue regressão de item que sempre falhou, e não diz o estado anterior.
3. **Comparação pareada: cada item desta execução contra o mesmo item na baseline da aplicação recebida, com regras explícitas por tipo de item.**

## Decisão

Opção 3, em `evals/gate.py` (`python evals/gate.py`), que roda a suíte (`evals/suite.py`) e compara o relatório com `evals/baselines/v1-baseline.json`, gerada uma vez na tag `v1-baseline` a partir de ambiente limpo.

O gate reprova (código de saída `1`) quando:

1. um avaliador não conseguiu executar (`ERROR`: conexão recusada, HTTP inesperado como o `502` da borda com o `app` parado, trace que não chega ao Jaeger, exceção no avaliador). Avaliador quebrado nunca conta como aprovado;
2. um item de incidente (`incident`) ou um ataque (`attack`) falha agora, qualquer que seja o estado na baseline;
3. um item que passava na baseline falha agora (regressão — golden e não regressões);
4. um item novo, sem estado na baseline, falha agora;
5. um item da baseline não existe mais na suíte.

Código de saída `2`: o próprio gate não roda (baseline ausente ou ilegível). A baseline é só lida: o gate e a suíte gravam em `evals/reports/` (fora do git) e nunca a regeneram; na `main`, ela não muda.

A saída lista cada item com o veredito, o estado na baseline e o de agora, e o motivo (e as razões do avaliador) de cada reprovação. O workflow `.github/workflows/gate.yml` sobe o compose e roda o mesmo comando em todo push e pull request, e guarda o relatório como artefato.

## Consequências

- Melhor: o PR que desfaz uma correção é reprovado nomeando o item e o incidente; quebrar o ambiente reprova em vez de passar.
- Pior: a suíte assume ambiente recém-subido (`docker compose down -v`), porque a aplicação não tem rota de reset dos caches; rodar duas vezes seguidas no mesmo volume não é suportado.
- Os itens são determinísticos (o provider é determinístico); não há tolerância a flutuação. Se um item ficar instável, a correção é no item, não no gate.

## Evidência

Roteiro do avaliador, passo 8:

```bash
docker compose down -v && docker compose up -d --build --wait
python evals/gate.py          # gate: APROVADO
docker compose down -v && git checkout --no-overlay v1-baseline -- app && docker compose up -d --build --wait
python evals/gate.py          # gate: REPROVADO, 12 itens de incidente e 6 ataques, com motivo
git checkout --no-overlay main -- app && docker compose down -v && docker compose up -d --build --wait
docker compose stop app
python evals/gate.py          # gate: REPROVADO, avaliador não conseguiu executar
```

Execuções do workflow: links no [README](../../README.md#gate-e-ci).
