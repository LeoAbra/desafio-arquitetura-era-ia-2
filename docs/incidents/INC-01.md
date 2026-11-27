# INC-01: colaborador da Boreal recebeu o benefício de outra empresa

Relato: [`incidents/INC-01-beneficio-de-outra-empresa.md`](../../incidents/INC-01-beneficio-de-outra-empresa.md)

## Reprodução (aplicação recebida, ambiente limpo)

```bash
docker compose down -v && cp .env.example .env && docker compose up -d --build --wait
curl -s -X POST localhost:8090/admin/reset && curl -s -X POST localhost:8091/admin/reset
ask() { curl -s localhost:8000/ask -H "Authorization: Bearer $1" -H "Content-Type: application/json" -d "{\"question\": \"$2\"}"; echo; }

ask tok-ana   "Qual o valor do vale-refeição?"
ask tok-bruno "qual é o valor do vale-refeição?"
ask tok-bruno "Qual o valor do vale-refeição?"
curl -s "localhost:8090/admin/calls?last=5"
```

## O que foi observado

| Pergunta | Quem | `origin` | Resposta |
|---|---|---|---|
| `Qual o valor do vale-refeição?` | Ana (estrela) | `model` | "O valor do vale-refeição da Padaria Estrela é de R$ 35,00 por dia útil trabalhado." fonte `vale-refeicao` |
| `qual é o valor do vale-refeição?` | Bruno (boreal) | `semantic_cache` | a mesma resposta da Estrela, fonte `vale-refeicao` |
| `Qual o valor do vale-refeição?` | Bruno (boreal) | `exact_cache` | a mesma resposta da Estrela, fonte `vale-refeicao` |

`/admin/calls` mostra uma única chamada `answer` (a da Ana, `input_tokens` 1646); as perguntas do Bruno geraram só chamadas `embed`. Ou seja, a resposta errada não veio do modelo nem do retrieval (que filtra por empresa, como o suporte verificou): veio do cache. O mesmo acontece no sentido contrário: a Ana recebe o auxílio home office da Boreal (`R$ 150,00`) se a Daniela perguntou antes.

## Causa

`app/assistant/cache.py`: a chave do cache exato é só a pergunta normalizada, e a busca do cache semântico não filtra por nada. A empresa não entra em nenhum dos dois.

```python
def exact_key(question: str) -> str:
    return hashlib.sha256(normalize(question).encode()).hexdigest()
...
"SELECT response, 1 - (embedding <=> %s::vector) AS similarity FROM semantic_cache "
"ORDER BY embedding <=> %s::vector LIMIT 1"
```

As tabelas `answer_cache` e `semantic_cache` (`app/assistant/db.py`) nem têm coluna de empresa, e `main.py` chama `cache.get_exact(question)` / `cache.get_semantic(embedding)` sem passar o `tenant_id`. A resposta depende da empresa (os documentos são dela), mas a chave não.

## Correção

ADR: [0001 — Chave e política do cache de respostas](../adr/0001-chave-e-politica-do-cache-de-respostas.md)

## Itens da suíte que cobrem o incidente

- `inc01-vr-boreal-depois-da-estrela` (cache semântico, Estrela → Boreal)
- `inc01-vr-boreal-mesma-frase` (cache exato, Estrela → Boreal)
- `inc01-home-office-estrela-depois-da-boreal` (Boreal → Estrela)
- Não regressão, sem campo de incidente: `nr-cache-exato-mesma-empresa`, `nr-cache-semantico-mesma-empresa` (o cache continua servindo outro colaborador da mesma empresa, sem nova chamada `answer`)
