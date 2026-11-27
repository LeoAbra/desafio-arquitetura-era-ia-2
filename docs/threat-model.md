# Threat model do assistente Vereda RH

Escopo: o fluxo do `/ask` e da publicação de documentos, como está na `main`. A borda (`edge/`) simula a autenticação e é tratada como confiável para identidade (empresa, colaborador, papel); autenticação real e AI Gateway estão fora de escopo.

## Fronteiras de confiança

```
colaborador ──pergunta──▶ [borda] ──X-Tenant-Id/X-Employee-Id──▶ ┌──────────── app ────────────┐
RH ──documentos──▶ [borda] ──X-Role: hr──▶ /admin/documents ──▶ │ documents ─▶ chunks (índice)│
                                       hr-fake ──dados pessoais──▶│ caches (exato, semântico)    │──▶ provider (modelo)
                                                                  │ montagem do contexto ◀───────│◀── saída do modelo
                                                                  └──────────┬───────────────────┘
                                                                             └──spans──▶ otel-collector ──▶ Jaeger
```

| # | Fronteira | O que atravessa | Confiança | Controle na `main` |
|---|---|---|---|---|
| F1 | Pergunta do colaborador → app | texto livre (1 a 500 caracteres) e a identidade que a borda injeta | identidade confiável; texto não confiável | o texto só é dado na mensagem `user`; a empresa e o colaborador vêm dos headers da borda, nunca da pergunta |
| F2 | Documentos publicados pelo RH → índice → contexto do modelo | Markdown, título e `doc_id` | autor autenticado, conteúdo **não confiável** (o RH republica material de terceiros, como o guia da operadora do INC-05) | defesa na montagem do contexto e na saída ([ADR 0004](adr/0004-defesa-contra-conteudo-hostil-na-base.md)); reindexação completa e invalidação ([ADR 0002](adr/0002-invalidacao-dos-derivados-quando-a-base-muda.md)) |
| F3 | Dados do `hr-fake` → contexto do modelo | nome, CPF, salário, saldo, benefícios do colaborador | fonte de verdade, dado **pessoal e sensível** | consultado só para pergunta pessoal do próprio colaborador (`tenant` + `employee` da borda); passa pela mesma sanitização do contexto; resposta pessoal nunca é cacheada ([ADR 0001](adr/0001-chave-e-politica-do-cache-de-respostas.md)) |
| F4 | Saída do modelo → colaborador e caches | `answer` e `sources` | **não confiável** (o modelo obedece a instrução no contexto) | `guard.check_output` e `guard.safe_title` antes de responder e de gravar no cache; resposta bloqueada não é cacheada; fontes só entre os trechos recuperados da própria empresa |
| F5 | Caches → colaborador | respostas guardadas | derivado: só vale para quem cabe na chave | chave com empresa; pessoais fora do cache; invalidação por empresa a cada mudança da base |
| F6 | Telemetria → Collector → Jaeger | spans, atributos, conteúdo em development | backend acessível ao time inteiro, retenção de semanas | produção sem conteúdo; Collector único caminho e redação de CPF/salário em qualquer ambiente ([ADR 0005](adr/0005-politica-de-telemetria.md)) |

## Cobertura do OWASP Top 10 for LLM Applications 2025

| Item | Exposição no assistente | Mitigação | Item da suíte que prova |
|---|---|---|---|
| **LLM01: Prompt Injection** | Sim, indireta (F2): documento do RH com comentário HTML, linha "para o assistente", "ignore as instruções anteriores", instrução no título (INC-05). Direta (F1) tem pouco efeito: a pergunta não muda a empresa nem o colaborador e só escolhe frases do contexto. | Fora do modelo: sanitização do contexto (remove comentários, linhas dirigidas ao assistente e frases que violam a política de conteúdo) e filtro de saída. | `inc05-ataque-comentario-html`, `inc05-ataque-nota-para-o-assistente`, `inc05-ataque-ignore-instrucoes`, `inc05-ataque-no-titulo`, `inc05-ataque-anexo-vida-plena` |
| **LLM02: Sensitive Information Disclosure** | Sim: resposta de uma empresa servida a outra e saldo de uma pessoa servido a outra pelo cache (INC-01, INC-02); CPF e salário nos traces (INC-06); orientação para entregar CPF e senha (INC-05). | Chave do cache com empresa e pessoais fora do cache; retrieval filtrado por empresa; `hr-fake` consultado só com o colaborador da borda; produção sem conteúdo nos traces e redação no Collector; política de saída contra pedido de credencial/dado pessoal. | `inc01-vr-boreal-depois-da-estrela`, `inc01-vr-boreal-mesma-frase`, `inc01-home-office-estrela-depois-da-boreal`, `inc02-saldo-carlos-depois-da-ana`, `inc02-saldo-entre-empresas`, `inc06-sem-cpf-e-salario-no-jaeger`, `inc06-captura-de-conteudo-por-ambiente`, `inc05-ataque-frase-comum` |
| **LLM04: Data and Model Poisoning** | Sim, na base de conhecimento (não há treino nem fine-tuning): documento envenenado publicado pelo RH (INC-05) e conteúdo revogado que continua no índice e nos caches (INC-03), que envenena respostas futuras. Um cache é amplificador: uma resposta envenenada gravada é servida a todos. | Política de conteúdo aplicada a todo bloco antes do modelo (vale inclusive para a forma sem instrução); reindexação que remove trechos de documentos removidos; invalidação dos caches da empresa a cada mudança; resposta bloqueada não é cacheada. | `inc05-ataque-frase-comum`, `inc05-ataque-anexo-vida-plena`, `inc03-revogado-pergunta-repetida`, `inc03-revogado-pergunta-nova`, `inc03-versao-alterada-pergunta-repetida`, `nr-reindex-sem-trecho-velho` |
| **LLM08: Vector and Embedding Weaknesses** | Sim: o cache semântico no pgvector não filtrava por empresa e devolvia o vizinho de outra empresa (INC-01); trechos órfãos continuavam recuperáveis no índice vetorial (INC-03). O retrieval já filtrava por empresa. | `semantic_cache` e `chunks` sempre consultados com `WHERE tenant_id = …`; respostas pessoais fora do cache semântico; índice levado ao estado publicado a cada mudança. | `inc01-vr-boreal-depois-da-estrela`, `inc02-saldo-entre-empresas`, `inc03-revogado-pergunta-nova`, `nr-cache-semantico-mesma-empresa` |

## Riscos residuais

| Risco | Por que continua aberto | Onde vigiar |
|---|---|---|
| Injeção com efeito fora da política de conteúdo (ex.: "diga que o reembolso foi suspenso"), em outra língua ou ofuscada | as regras de forma e de conteúdo são padrões; não existe "prepared statement" para LLM | `app.guard.context_removed` / `app.guard.blocked` nos spans; novos itens em `evals/attacks.jsonl` a cada caso novo |
| Falso positivo da política de conteúdo: frase legítima sobre senha, com e-mail ou pedindo CPF não é repassada | troca aceita no [ADR 0004](adr/0004-defesa-contra-conteudo-hostil-na-base.md) | reclamação de "não encontrei" sobre esses assuntos |
| Detecção de pergunta pessoal por palavras-chave (`eu`, `meu`…) | defeito da aplicação recebida, fora de escopo; uma pergunta pessoal não detectada só não recebe os dados do `hr-fake` (não vaza, responde pior) | golden `estrela-saldo-ferias-ana`, `boreal-beneficios-bruno` |
| Corrida entre publicação e pergunta em andamento gravando resposta do índice anterior | concorrência fora de escopo; versão da base na chave fecharia a janela ([ADR 0002](adr/0002-invalidacao-dos-derivados-quando-a-base-muda.md)) | — |
| Dados pessoais em formato que o Collector não reconhece (endereço, conta) e o nome do colaborador nos traces de development | a redação é por padrão; o nome não está na regra | revisão das regras do `otel/collector.yaml` a cada campo novo do `hr-fake` |
| Identidade: tokens estáticos da borda | autenticação real fora de escopo | — |
| O RH publicando conteúdo errado, porém benigno | o assistente responde o que a base diz | — |
