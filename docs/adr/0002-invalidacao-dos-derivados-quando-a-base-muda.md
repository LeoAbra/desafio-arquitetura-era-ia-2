# 0002. Reindexar até o estado publicado e invalidar os caches da empresa quando a base muda

- Status: aceita
- Data: 2026-11-27
- Nível: solução
- Incidente: [INC-03](../incidents/INC-03.md)

## Contexto

Depois que o RH da Estrela removeu o `auxilio-academia`, o assistente continuou respondendo `R$ 80,00` e citando o documento:

- pergunta já feita: `origin: exact_cache` — a resposta guardada antes da remoção;
- pergunta nova: `origin: model` — `reindex` percorria só os documentos existentes, e os 2 trechos do documento removido continuavam em `chunks` e eram recuperados.

Com a versão alterada do `home-office` da Boreal (R$ 150,00 → R$ 200,00) a reindexação trocava os trechos, mas a pergunta já feita continuava vindo do cache com R$ 150,00.

O índice e as respostas guardadas são estado derivado dos documentos da empresa. Os dois precisam acompanhar a base, inclusive na remoção.

## Opções consideradas

1. Só apagar os trechos órfãos no `reindex`. Resolve a pergunta nova; a já feita continua vindo do cache.
2. TTL curto nos caches. A resposta revogada continua sendo servida até expirar, e o custo sobe para todos.
3. Invalidar só as entradas de cache que citam o documento alterado. Não cobre resposta que deveria passar a citar um documento novo (ex.: "Não encontrei" guardado antes da publicação) nem a que deixou de existir sem citar fonte.
4. Versão da base na chave do cache (contador por empresa incrementado a cada mudança). Correta e sem corrida, mas deixa entradas mortas e exige mais uma consulta por pergunta.
5. **`reindex` idempotente que leva o índice ao estado publicado (novos, alterados e removidos, numa transação) e, se algo mudou, descarta todas as respostas guardadas da empresa nos dois caches.**

## Decisão

Opção 5, em `app/assistant/knowledge.py` (`reindex`) e `app/assistant/cache.py` (`invalidate_tenant`):

- documentos sem mudança de hash são pulados (idempotente);
- documentos novos ou alterados têm os trechos apagados e reinseridos;
- trechos de `doc_id` que não existem mais em `documents` são apagados (`summary["removed"]`);
- as escritas no índice acontecem numa única transação, depois de calcular os embeddings;
- se houve `indexed` ou `removed`, `DELETE FROM answer_cache/semantic_cache WHERE tenant_id = …` (`summary["cache_invalidated"]`).

Como todo caminho que muda a base (publicação, remoção, carga inicial) passa por `reindex`, nenhum deles consegue mudar a base sem invalidar os derivados. A invalidação é por empresa: a base de uma empresa não afeta a resposta de outra ([ADR 0001](0001-chave-e-politica-do-cache-de-respostas.md)).

## Consequências

- Melhor: depois de uma remoção ou versão nova, nem pergunta nova nem pergunta repetida devolvem o conteúdo antigo; o retorno do `POST`/`DELETE /admin/documents` mostra o que foi reindexado, removido e invalidado.
- Pior: cada publicação esvazia o cache da empresa inteira; as próximas perguntas de política daquela empresa vão ao modelo uma vez. Publicações são raras perto de perguntas, então o custo é pequeno.
- Residual: uma pergunta em andamento durante a publicação pode gravar no cache uma resposta montada com o índice anterior (concorrência, fora de escopo). A opção 4 fecharia essa janela se ela passar a importar.

## Evidência

Itens da suíte: `inc03-revogado-pergunta-repetida`, `inc03-revogado-pergunta-nova`, `inc03-versao-alterada-pergunta-repetida` (falham na baseline, passam na `main`) e `nr-reindex-sem-trecho-velho` (versão sem uma seção não deixa o trecho antigo).

Pela borda (roteiro do avaliador, passo 4):

```bash
ask tok-ana "Qual o valor do auxílio academia?"      # R$ 80,00
curl -s -X DELETE localhost:8000/admin/documents/auxilio-academia -H "Authorization: Bearer tok-rh-estrela"
# {"doc_id":"auxilio-academia","reindex":{"indexed":[],"unchanged":[...],"removed":["auxilio-academia"],"cache_invalidated":N}}
ask tok-carlos "Qual o valor do auxílio academia?"   # origin model, sem R$ 80,00 e sem o documento
docker compose exec -T postgres psql -U vereda -c "select count(*) from chunks where doc_id='auxilio-academia'"   # 0
```
