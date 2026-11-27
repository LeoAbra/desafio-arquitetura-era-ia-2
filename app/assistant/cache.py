"""Cache de respostas: exato (chave pela empresa + pergunta) e semântico (pgvector, filtrado pela empresa).

Política (ADR 0001): a chave contém tudo de que a resposta depende. Respostas de política dependem
da empresa (os documentos são dela) e da versão da base (invalidada a cada mudança, ADR 0002).
Respostas montadas com dados do colaborador (hr-fake) não são cacheadas: quem decide isso é main.ask.
"""

import hashlib
import re

from psycopg.types.json import Jsonb

from . import config, db


def normalize(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().lower()


def exact_key(tenant_id: str, question: str) -> str:
    return hashlib.sha256(normalize(question).encode()).hexdigest()  # INC-01 desfeito: empresa fora da chave


def get_exact(tenant_id: str, question: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT response FROM answer_cache WHERE cache_key = %s",
                           (exact_key(tenant_id, question),)).fetchone()
    return row[0] if row else None


def put_exact(tenant_id: str, question: str, response: dict) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO answer_cache (cache_key, tenant_id, question, response) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (cache_key) DO UPDATE SET response = EXCLUDED.response, created_at = NOW()",
            (exact_key(tenant_id, question), tenant_id, question, Jsonb(response)),
        )


def get_semantic(tenant_id: str, embedding: list[float]) -> tuple[dict, float] | None:
    vector = db.vector_literal(embedding)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT response, 1 - (embedding <=> %s::vector) AS similarity FROM semantic_cache "
            "ORDER BY embedding <=> %s::vector LIMIT 1",
            (vector, vector),
        ).fetchone()
    if row and row[1] >= config.SEMANTIC_CACHE_THRESHOLD:
        return row[0], float(row[1])
    return None


def put_semantic(tenant_id: str, question: str, embedding: list[float], response: dict) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO semantic_cache (tenant_id, question, embedding, response) "
                     "VALUES (%s, %s, %s::vector, %s)",
                     (tenant_id, question, db.vector_literal(embedding), Jsonb(response)))


def invalidate_tenant(tenant_id: str) -> int:
    """Descarta as respostas guardadas da empresa: são derivadas da base que acabou de mudar (ADR 0002)."""
    with db.connect() as conn:
        exact = conn.execute("DELETE FROM answer_cache WHERE tenant_id = %s", (tenant_id,)).rowcount
        semantic = conn.execute("DELETE FROM semantic_cache WHERE tenant_id = %s", (tenant_id,)).rowcount
    return exact + semantic
