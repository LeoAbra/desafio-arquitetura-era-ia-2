# INC-03: benefício revogado continua sendo oferecido

Relato: [`incidents/INC-03-politica-revogada.md`](../../incidents/INC-03-politica-revogada.md)

## Reprodução (aplicação recebida, ambiente limpo)

```bash
ask tok-ana    "Qual o valor do auxílio academia?"
curl -s -X DELETE localhost:8000/admin/documents/auxilio-academia -H "Authorization: Bearer tok-rh-estrela"
ask tok-carlos "Qual o valor do auxílio academia?"
ask tok-ana    "Como funciona o auxílio academia da empresa?"
docker compose exec -T postgres psql -U vereda -c "select doc_id, count(*) from chunks where tenant_id='estrela' group by 1 order by 1"
docker compose exec -T postgres psql -U vereda -c "select doc_id from documents where tenant_id='estrela' order by 1"

ask tok-bruno "Qual o valor do auxílio home office?"
curl -s localhost:8000/admin/documents -H "Authorization: Bearer tok-rh-boreal" -H "Content-Type: application/json" \
  -d '{"doc_id": "home-office", "title": "Trabalho híbrido", "content": "## Auxílio home office\nO auxílio home office é de R$ 200,00 por mês para quem trabalha em regime híbrido."}'
ask tok-daniela "Qual o valor do auxílio home office?"
```

## O que foi observado

- O `DELETE` responde `{"doc_id":"auxilio-academia","reindex":{"indexed":[],"unchanged":["ferias","home-office","plano-saude","vale-refeicao"]}}`: a reindexação não menciona o documento removido.
- A pergunta repetida (Carlos) volta com `origin: exact_cache` e "O auxílio academia é de R$ 80,00 por mês, pago junto com o salário.", fonte `auxilio-academia`.
- A pergunta nova (`Como funciona o auxílio academia da empresa?`) volta com `origin: model` e a mesma frase, fonte `auxilio-academia`: o modelo recebeu o trecho do documento removido.
- No banco, `documents` não tem mais `auxilio-academia`, mas `chunks` ainda tem 2 trechos dele.
- Versão alterada: depois de publicar o `home-office` com R$ 200,00, a reindexação troca os trechos (`"indexed":["home-office"]`), mas a Daniela recebe `origin: exact_cache` com "R$ 150,00" — a resposta guardada para o Bruno antes da mudança.

## Causa

Duas metades, as duas sobre estado derivado que não acompanha a base:

1. `app/assistant/knowledge.py`, `reindex`: percorre só os documentos que existem. Trata documento novo e alterado, mas não o que deixou de existir; os trechos dele ficam em `chunks` e continuam sendo recuperados.

```python
documents = conn.execute("SELECT doc_id, title, content, content_hash FROM documents WHERE tenant_id = %s", ...)
indexed = {r[0]: r[1] for r in conn.execute("SELECT DISTINCT doc_id, doc_hash FROM chunks ...")}
for doc_id, title, content, doc_hash in documents:      # doc removido nunca entra no laço
    ...
```

2. `app/assistant/main.py` / `cache.py`: as respostas guardadas nos caches exato e semântico também são derivadas dos documentos, mas nada as invalida quando o RH publica ou remove um documento (`publish_document` e `revoke_document` só chamam `knowledge.reindex`). Pergunta já feita continua recebendo a resposta antiga, de documento removido ou da versão anterior.

## Correção

ADR: [0002 — Invalidação dos derivados quando a base muda](../adr/0002-invalidacao-dos-derivados-quando-a-base-muda.md)

## Itens da suíte que cobrem o incidente

- `inc03-revogado-pergunta-repetida`
- `inc03-revogado-pergunta-nova`
- `inc03-versao-alterada-pergunta-repetida`
- Não regressão, sem campo de incidente: `nr-reindex-sem-trecho-velho` (versão que remove uma seção não deixa o trecho antigo)
