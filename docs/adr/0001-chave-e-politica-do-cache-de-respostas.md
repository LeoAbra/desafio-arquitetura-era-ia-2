# 0001. Incluir a empresa na chave do cache e não cachear respostas pessoais

- Status: aceita
- Data: 2026-11-27
- Nível: solução
- Incidentes: [INC-01](../incidents/INC-01.md), [INC-02](../incidents/INC-02.md)

## Contexto

O cache exato (`answer_cache`) e o semântico (`semantic_cache`) usavam só a pergunta como chave. A resposta, porém, depende de mais coisas:

- da empresa, porque o contexto são os documentos dela (INC-01: o Bruno, da Boreal, recebeu `R$ 35,00` da Estrela com `origin: semantic_cache`, e a mesma frase exata veio com `origin: exact_cache`);
- nas perguntas pessoais, do colaborador e do estado atual dele no `hr-fake` (INC-02: o Carlos recebeu os 18 dias da Ana com `origin: exact_cache`, e uma correção de saldo no `hr-fake` nunca chegava à resposta).

Uma resposta errada no cache é servida a todo mundo que pergunta algo parecido. Ao mesmo tempo, os dois caches existem por causa de custo (CHANGELOG 07/01 e 07/20) e precisam continuar servindo o que pode ser reaproveitado: a mesma pergunta de política, feita por outro colaborador da mesma empresa.

## Opções consideradas

1. Desligar os caches. Resolve INC-01 e INC-02 e devolve o custo do INC-04; proibido pelas restrições.
2. Chave = empresa + pergunta para tudo, inclusive pessoais. Resolve o INC-01, mas a resposta da Ana continua servida ao Carlos (mesma empresa) e fica desatualizada.
3. Chave = empresa + colaborador + pergunta para as pessoais. Resolve a troca de pessoa, mas continua desatualizada quando o `hr-fake` muda e grava dados pessoais (saldo, benefícios, eventualmente salário) em tabela de cache.
4. Chave com versão dos dados do colaborador (hash do registro do `hr-fake`) para as pessoais. Correta, mas exige consultar o `hr-fake` antes do cache em toda pergunta pessoal e ainda persiste dado pessoal no cache.
5. **Empresa na chave dos dois caches para respostas de política; respostas pessoais não são lidas nem gravadas no cache.**

## Decisão

Opção 5.

- Cache exato: `cache_key = sha256(tenant_id + "\0" + pergunta normalizada)`, com coluna `tenant_id` (`app/assistant/cache.py`, `app/assistant/db.py`).
- Cache semântico: coluna `tenant_id` e busca do vizinho mais próximo com `WHERE tenant_id = %s`; o limiar de 0,92 fica.
- Pergunta pessoal (colaborador identificado e `hr.is_personal(pergunta)`, o mesmo critério que decide anexar os dados do `hr-fake` ao contexto): `main.ask` pula as duas consultas ao cache e não grava a resposta (`app.cache.eligible=false` no span raiz). Ela sempre vai ao modelo com os dados atuais do `hr-fake`.
- A versão da base da empresa também é parte do que a resposta depende; ela é tratada por invalidação ([ADR 0002](0002-invalidacao-dos-derivados-quando-a-base-muda.md)).

O que torna uma resposta reaproveitável é ela ser a mesma para todo mundo que cabe na chave. Para política, isso é "mesma empresa e mesma base"; para dado pessoal, só "mesma pessoa e mesmo instante", o que não compensa cachear.

## Consequências

- Melhor: nenhuma resposta atravessa empresas nem pessoas; saldo alterado no `hr-fake` aparece na pergunta seguinte; nenhum dado pessoal fica gravado nos caches.
- Pior: pergunta pessoal sempre custa uma chamada `answer` (com o prefixo do system prompt lido do cache do provider, ~US$ 0,0013 em vez de ~US$ 0,0045, [ADR 0003](0003-estrutura-do-prompt-para-o-cache-do-provider.md)).
- A detecção de pergunta pessoal por palavras-chave (`eu`, `meu`, `minha`…) continua como recebida (fora de escopo). Uma pergunta pessoal não detectada não recebe dados do `hr-fake` no contexto, então a resposta dela também não depende do colaborador e pode ser cacheada por empresa.
- Mudar o esquema exige subir com volume limpo (`docker compose down -v`).

## Evidência

Itens da suíte: `inc01-vr-boreal-depois-da-estrela`, `inc01-vr-boreal-mesma-frase`, `inc01-home-office-estrela-depois-da-boreal`, `inc02-saldo-carlos-depois-da-ana`, `inc02-saldo-entre-empresas`, `inc02-saldo-atualizado-no-hr` (falham na baseline, passam na `main`) e `nr-cache-exato-mesma-empresa`, `nr-cache-semantico-mesma-empresa` (passam nas duas: o cache continua servindo a mesma empresa sem nova chamada `answer`).

Pela borda (roteiro do avaliador, passos 2 e 3):

```bash
ask tok-ana    "Qual o valor do vale-refeição?"     # origin model, R$ 35,00
ask tok-bruno  "qual é o valor do vale-refeição?"   # origin model, resposta da Boreal (sem R$ 35,00, sem vale-refeicao)
ask tok-carlos "Qual o valor do vale-refeição?"     # origin exact_cache, R$ 35,00
curl -s "localhost:8090/admin/calls?last=10"        # depois da pergunta do Carlos, nenhuma chamada answer nova
ask tok-ana    "Quantos dias de férias eu tenho?"   # 18 dias
ask tok-carlos "Quantos dias de férias eu tenho?"   # 7 dias
docker compose exec -T postgres psql -U vereda -c "select tenant_id, question from answer_cache"   # nenhuma pergunta pessoal
```
