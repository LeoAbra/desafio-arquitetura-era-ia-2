# Changelog do assistente

## 2026-11-27
- Cache exato e semântico separados por empresa; respostas montadas com dados do sistema de RH deixam de ser cacheadas (INC-01, INC-02; ADR 0001).
- A reindexação remove os trechos de documentos removidos e invalida as respostas guardadas da empresa quando a base muda (INC-03; ADR 0002).
- A data e a hora atuais passam para o fim das instruções do modelo, preservando o prefixo do cache de prompt do provider (INC-04; ADR 0003).
- O conteúdo publicado pelo RH é tratado como dado: defesa na montagem do contexto e na saída (INC-05; ADR 0004).
- Traces via OpenTelemetry Collector, com um span por etapa; conteúdo só em `development`; CPF e salário redigidos no Collector (INC-06; ADR 0005).

## 2026-08-12
- O assistente passa a receber a data e a hora atuais no início das instruções do modelo. Pedido do produto: responder perguntas que dependem do dia, como prazos de solicitação de férias.

## 2026-07-20
- Cache semântico de respostas no pgvector: perguntas parecidas reaproveitam uma resposta já gerada (similaridade mínima de 0,92). Objetivo: reduzir custo e tempo de resposta.

## 2026-07-01
- Cache exato de respostas: a mesma pergunta não chama o modelo de novo.

## 2026-06-10
- O RH de cada empresa publica e revoga documentos pelo painel (`/admin/documents`), com reindexação automática.

## 2026-05-15
- Traces enviados ao Jaeger com a pergunta, o prompt e a resposta, para facilitar o atendimento do suporte.

## 2026-05-02
- Lançamento do assistente para as duas primeiras empresas clientes: Padaria Estrela e Metalúrgica Boreal.
