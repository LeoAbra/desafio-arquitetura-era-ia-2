# INC-02: saldo de férias de outra pessoa

Relato: [`incidents/INC-02-saldo-de-ferias-de-outra-pessoa.md`](../../incidents/INC-02-saldo-de-ferias-de-outra-pessoa.md)

## Reprodução (aplicação recebida, ambiente limpo)

```bash
curl -s -X POST localhost:8090/admin/reset && curl -s -X POST localhost:8091/admin/reset
ask tok-ana    "Quantos dias de férias eu tenho?"
ask tok-carlos "Quantos dias de férias eu tenho?"
curl -s -X PATCH localhost:8091/admin/tenants/estrela/employees/E001 -H "Content-Type: application/json" -d '{"vacation_days": 3}'
ask tok-ana    "Quantos dias de férias eu tenho?"
curl -s localhost:8091/tenants/estrela/employees/E002
```

## O que foi observado

| Pergunta | Quem | `origin` | Resposta | Verdade no `hr-fake` |
|---|---|---|---|---|
| `Quantos dias de férias eu tenho?` | Ana (E001) | `model` | "Saldo de férias disponível: 18 dias." | 18 |
| `Quantos dias de férias eu tenho?` | Carlos (E002) | `exact_cache` | "Saldo de férias disponível: 18 dias." | 7 |
| (depois do PATCH da Ana para 3 dias) | Ana (E001) | `exact_cache` | "Saldo de férias disponível: 18 dias." | 3 |

O Bruno, de outra empresa, recebe o mesmo "18 dias" pela mesma chave. O `hr-fake` devolve o saldo certo de cada um; a resposta errada vem do cache, sem chamada `answer` nova em `/admin/calls`.

## Causa

`app/assistant/main.py`: o cache é consultado antes de saber se a pergunta é pessoal, e a resposta gerada com os dados do colaborador é gravada nos dois caches com a mesma chave das perguntas de política (só o texto, ver INC-01).

```python
cached = cache.get_exact(question)          # antes de qualquer verificação
...
if x_employee_id and hr.is_personal(question):
    employee = hr.get_employee(tenant_id, x_employee_id)
...
cache.put_exact(question, response)         # grava também a resposta pessoal
cache.put_semantic(question, embedding, response)
```

Uma resposta montada com dados do `hr-fake` é verdade só para quem perguntou e só enquanto o `hr-fake` não mudar. Nem o colaborador nem a versão dos dados dele entram na chave, e não há expiração: a resposta é servida a outras pessoas e fica desatualizada para sempre.

## Correção

ADR: [0001 — Chave e política do cache de respostas](../adr/0001-chave-e-politica-do-cache-de-respostas.md)

## Itens da suíte que cobrem o incidente

- `inc02-saldo-carlos-depois-da-ana`
- `inc02-saldo-entre-empresas`
- `inc02-saldo-atualizado-no-hr`
- Golden original que continua passando: `estrela-saldo-ferias-ana`, `boreal-beneficios-bruno`
