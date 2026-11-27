import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from . import cache, config, db, guard, hr, knowledge, llm, retrieval, telemetry
from .prompt import build_messages
from .schemas import AskRequest, AskResponse, DocumentRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    telemetry.setup()
    db.init()
    knowledge.seed()
    yield


app = FastAPI(title="Vereda RH - assistente", lifespan=lifespan)


def require_tenant(tenant_id: str | None) -> str:
    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenant não identificado")
    return tenant_id


def require_hr(role: str | None) -> None:
    if role != "hr":
        raise HTTPException(status_code=403, detail="apenas o RH da empresa pode gerenciar documentos")


def parse_model_output(text: str) -> tuple[str, list[str]]:
    try:
        data = json.loads(text)
        return str(data["answer"]), [str(s) for s in data.get("sources", [])]
    except (ValueError, KeyError, TypeError):
        return text, []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest,
        x_tenant_id: str | None = Header(default=None),
        x_employee_id: str | None = Header(default=None)):
    tenant_id = require_tenant(x_tenant_id)
    question = body.question
    tracer = telemetry.tracer

    with tracer.start_as_current_span("ask") as root:
        root.set_attribute("app.tenant_id", tenant_id)
        root.set_attribute("app.employee_id", x_employee_id or "")
        if config.CAPTURE_CONTENT:
            root.set_attribute("app.question", question)

        def finish(response: dict, origin: str) -> dict:
            root.set_attribute("app.origin", origin)
            if config.CAPTURE_CONTENT:
                root.set_attribute("app.answer", response["answer"])
            return {**response, "origin": origin}

        # Resposta pessoal (montada com dados do hr-fake) é verdade só para quem perguntou e só
        # enquanto o hr-fake não mudar: não é lida nem gravada nos caches (ADR 0001).
        personal = bool(x_employee_id) and hr.is_personal(question)
        root.set_attribute("app.cache.eligible", not personal)

        if not personal:
            with tracer.start_as_current_span("cache.exact.lookup") as span:
                cached = cache.get_exact(tenant_id, question)
                span.set_attribute("app.cache.hit", cached is not None)
            if cached is not None:
                return finish(cached, "exact_cache")

        embedding = llm.embed([question])[0]

        if not personal:
            with tracer.start_as_current_span("cache.semantic.lookup") as span:
                semantic = cache.get_semantic(tenant_id, embedding)
                span.set_attribute("app.cache.hit", semantic is not None)
                if semantic is not None:
                    span.set_attribute("app.cache.similarity", semantic[1])
            if semantic is not None:
                return finish(semantic[0], "semantic_cache")

        employee = None
        if personal:
            with tracer.start_as_current_span("hr.get_employee") as span:
                employee = hr.get_employee(tenant_id, x_employee_id)
                span.set_attribute("app.hr.found", employee is not None)

        with tracer.start_as_current_span("retrieval.search") as span:
            chunks = retrieval.search(tenant_id, embedding)
            span.set_attribute("app.retrieval.top_k", config.RETRIEVAL_TOP_K)
            span.set_attribute("app.retrieval.doc_ids", [c["doc_id"] for c in chunks])

        with tracer.start_as_current_span("prompt.build") as span:
            messages, removed = build_messages(tenant_id, question, chunks, employee)
            span.set_attribute("app.guard.context_removed", removed)

        text, _usage = llm.chat(messages)
        answer, cited = parse_model_output(text)

        with tracer.start_as_current_span("guard.output") as span:
            answer, blocked = guard.check_output(answer)
            span.set_attribute("app.guard.blocked", blocked is not None)
            if blocked:
                span.set_attribute("app.guard.reason", blocked)
                cited = []
            titles = {c["doc_id"]: guard.safe_title(c["title"], c["doc_id"]) for c in chunks}
            sources = [{"document_id": doc_id, "title": titles[doc_id]} for doc_id in dict.fromkeys(cited)
                       if doc_id in titles]
        response = {"answer": answer, "sources": sources}

        if not personal and not blocked:
            with tracer.start_as_current_span("cache.store"):
                cache.put_exact(tenant_id, question, response)
                cache.put_semantic(tenant_id, question, embedding, response)

        return finish(response, "model")


@app.get("/admin/documents")
def list_documents(x_tenant_id: str | None = Header(default=None), x_role: str | None = Header(default=None)):
    tenant_id = require_tenant(x_tenant_id)
    require_hr(x_role)
    return knowledge.list_documents(tenant_id)


@app.post("/admin/documents")
def publish_document(body: DocumentRequest,
                     x_tenant_id: str | None = Header(default=None),
                     x_role: str | None = Header(default=None)):
    tenant_id = require_tenant(x_tenant_id)
    require_hr(x_role)
    knowledge.upsert_document(tenant_id, body.doc_id, body.title, body.content)
    return {"doc_id": body.doc_id, "reindex": knowledge.reindex(tenant_id)}


@app.delete("/admin/documents/{doc_id}")
def revoke_document(doc_id: str,
                    x_tenant_id: str | None = Header(default=None),
                    x_role: str | None = Header(default=None)):
    tenant_id = require_tenant(x_tenant_id)
    require_hr(x_role)
    if not knowledge.delete_document(tenant_id, doc_id):
        raise HTTPException(status_code=404, detail="documento não encontrado")
    return {"doc_id": doc_id, "reindex": knowledge.reindex(tenant_id)}
