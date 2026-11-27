"""Defesa contra conteúdo hostil na base de conhecimento (ADR 0004).

A defesa fica fora do modelo, em dois pontos:

1. Montagem do contexto (sanitize_context): o que o RH publica é dado, nunca instrução. Antes de
   entrar no prompt, cada bloco do contexto perde
   - o que o colaborador e o RH não veem no documento renderizado (comentários HTML);
   - as linhas dirigidas ao assistente ou que tentam anular instruções;
   - as frases que orientam a entregar credenciais ou dados pessoais, ou que apontam para um
     e-mail: o assistente nunca repassa esse tipo de orientação, venha ela em forma de instrução
     ou de frase comum do documento.
2. Saída (check_output / safe_title): a resposta e os títulos das fontes passam pela mesma política
   de conteúdo antes de chegar ao colaborador e aos caches.

As regras de conteúdo olham para o efeito (pedir senha, CPF ou e-mail externo), não para a forma do
ataque; as regras de forma (comentário, linha dirigida ao assistente) são uma camada a mais.
"""

import re

SAFE_ANSWER = ("Não posso repassar essa orientação. Para esse assunto, procure o RH da sua empresa "
               "pelos canais oficiais.")

HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)

# Linhas que falam com o assistente, e não com o colaborador.
ADDRESSED_TO_ASSISTANT = re.compile(
    r"(?:\b(?:para|ao|à)\s+(?:o\s+|a\s+)?(?:assistente|chatbot|ia)\b"
    r"|\b(?:assistente|chatbot)\s*:"
    r"|\b(?:ignore|ignorar|desconsidere|desconsiderar|esque[cç]a|esquecer)\b.{0,40}\binstru[cç]"
    r"|\binstru[cç](?:ão|ao|ões|oes)\s+anteriores\b)",
    re.IGNORECASE,
)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CREDENTIAL = re.compile(r"\b(?:senhas?|password|passcode|pin|token de acesso|c[oó]digo de (?:verifica|seguran)\w*)\b",
                        re.IGNORECASE)
PERSONAL_DATA_REQUEST = re.compile(
    r"\b(?:envi|mand|encaminh|inform|compartilh|forne[cç]|repass|digit)\w*\b.{0,80}"
    r"\b(?:cpf|rg|dados banc[aá]rios|n[uú]mero do cart[aã]o|cart[aã]o de cr[eé]dito|ag[eê]ncia e conta)\b",
    re.IGNORECASE,
)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def violates_content_policy(text: str) -> str | None:
    """O motivo, quando o texto orienta a entregar credenciais/dados pessoais ou aponta para um e-mail."""
    if EMAIL.search(text):
        return "e-mail"
    if CREDENTIAL.search(text):
        return "credencial"
    if PERSONAL_DATA_REQUEST.search(text):
        return "pedido de dado pessoal"
    return None


def sanitize_text(text: str) -> tuple[str, int]:
    """O texto sem o conteúdo hostil e quantos trechos foram removidos."""
    removed = len(HTML_COMMENT.findall(text))
    text = HTML_COMMENT.sub("", text)
    lines = []
    for line in text.splitlines():
        if ADDRESSED_TO_ASSISTANT.search(line):
            removed += 1
            continue
        kept = []
        for sentence in SENTENCE_SPLIT.split(line):
            if violates_content_policy(sentence):
                removed += 1
            else:
                kept.append(sentence)
        cleaned = " ".join(kept).rstrip()
        if line.strip() and not cleaned.strip():
            continue
        lines.append(cleaned)
    return "\n".join(lines), removed


def sanitize_context(blocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    """Aplica a política a cada bloco (source, texto) do contexto antes de ele chegar ao modelo."""
    result, removed = [], 0
    for source, text in blocks:
        clean, n = sanitize_text(text)
        removed += n
        result.append((source, clean))
    return result, removed


def check_output(answer: str) -> tuple[str, str | None]:
    """A resposta que pode chegar ao colaborador e, se foi bloqueada, o motivo."""
    if HTML_COMMENT.search(answer) or ADDRESSED_TO_ASSISTANT.search(answer):
        return SAFE_ANSWER, "instrução"
    reason = violates_content_policy(answer)
    if reason:
        return SAFE_ANSWER, reason
    return answer, None


def safe_title(title: str, doc_id: str) -> str:
    """O título da fonte mostrado ao colaborador; cai para o doc_id se o título violar a política."""
    if HTML_COMMENT.search(title) or ADDRESSED_TO_ASSISTANT.search(title) or violates_content_policy(title):
        return doc_id
    return title
