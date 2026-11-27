"""Suíte de avaliação do assistente Vereda RH.

Roda contra o ambiente do compose, sempre pela borda (localhost:8000), com avaliadores
determinísticos em código. Lê os instrumentos de prova (/admin/calls do provider-fake,
hr-fake e a API do Jaeger) para as verificações. Só usa a biblioteca padrão do Python.

Uso:
    python evals/suite.py                      # grava evals/reports/<timestamp>.json e latest.json
    python evals/suite.py --output caminho.json

Cada item termina em PASS, FAIL ou ERROR. ERROR significa que o avaliador não conseguiu
executar (serviço fora do ar, resposta HTTP inesperada, trace que não chegou): nunca conta
como aprovado.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
KB_DIR = ROOT / "app" / "knowledge_base"

EDGE = os.environ.get("EDGE_URL", "http://localhost:8000")
PROVIDER = os.environ.get("PROVIDER_URL", "http://localhost:8090")
HR = os.environ.get("HR_URL", "http://localhost:8091")
JAEGER = os.environ.get("JAEGER_URL", "http://localhost:16686")
SERVICE = "vereda-assistant"

# A ordem importa: o golden roda primeiro, como no evals/README.md (banco recém-criado).
SOURCES = ["golden.jsonl", "incidents.jsonl", "attacks.jsonl", "checks.jsonl"]

CPF_RE = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
SALARIES = ["4.850,00", "3.200,00", "7.400,00", "5.950,00"]


class EvaluatorError(Exception):
    """O avaliador não conseguiu executar: o item vira ERROR, nunca PASS."""


# --------------------------------------------------------------------------- HTTP

def http(method: str, url: str, body=None, headers: dict | None = None, timeout: float = 30.0):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise EvaluatorError(f"{method} {url} falhou: {exc}") from exc
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text) if text else None
    except ValueError:
        payload = text
    return status, payload


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- estado

def kb_document(tenant: str, doc_id: str) -> dict:
    """Lê o documento original da base (mesma regra de front matter da aplicação)."""
    text = (KB_DIR / tenant / f"{doc_id}.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    title, body = doc_id, text
    if text.startswith("---\n"):
        header, _, body = text[4:].partition("\n---\n")
        for line in header.splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "title" and value.strip():
                title = value.strip()
    return {"doc_id": doc_id, "title": title, "content": body.strip()}


def answer_calls() -> list[dict]:
    status, calls = http("GET", f"{PROVIDER}/admin/calls?last=5000")
    if status != 200 or not isinstance(calls, list):
        raise EvaluatorError(f"/admin/calls respondeu {status}")
    return [c for c in calls if c.get("task") == "answer"]


# --------------------------------------------------------------------------- Jaeger

def jaeger_traces(params: dict) -> list[dict]:
    query = urllib.parse.urlencode({"service": SERVICE, **params})
    status, payload = http("GET", f"{JAEGER}/api/traces?{query}")
    if status != 200 or not isinstance(payload, dict):
        raise EvaluatorError(f"API do Jaeger respondeu {status}")
    return payload.get("data") or []


def tags_of(obj: dict) -> dict:
    return {t["key"]: t.get("value") for t in obj.get("tags", [])}


def root_span(trace: dict) -> dict | None:
    ids = {s["spanID"] for s in trace["spans"]}
    for span in trace["spans"]:
        parents = [r["spanID"] for r in span.get("references", []) if r.get("refType") == "CHILD_OF"]
        if not parents or not any(p in ids for p in parents):
            if span["operationName"] == "ask":
                return span
    return None


def wait_trace(since_us: int, employee: str | None, timeout: float = 40.0) -> dict:
    """Espera o trace do /ask feito depois de since_us chegar ao Jaeger (via app/Collector)."""
    deadline = time.time() + timeout
    params = {"start": since_us, "end": int((time.time() + 600) * 1_000_000), "limit": 50}
    while time.time() < deadline:
        found = []
        for trace in jaeger_traces(params):
            root = root_span(trace)
            if root is None or root["startTime"] < since_us:
                continue
            if employee and tags_of(root).get("app.employee_id") not in (None, employee):
                continue
            found.append((root["startTime"], trace))
        if found:
            # dá tempo para os spans filhos que ainda estejam no batch
            time.sleep(2.0)
            found.sort(key=lambda x: x[0])
            trace_id = found[-1][1]["traceID"]
            status, payload = http("GET", f"{JAEGER}/api/traces/{trace_id}")
            if status == 200 and payload and payload.get("data"):
                return payload["data"][0]
            return found[-1][1]
        time.sleep(1.0)
    raise EvaluatorError("o trace da pergunta não chegou ao Jaeger em 40 s")


def process_env(trace: dict) -> str:
    for process in trace.get("processes", {}).values():
        env = tags_of(process).get("deployment.environment.name") or tags_of(process).get("deployment.environment")
        if env:
            return str(env)
    return "production"


# --------------------------------------------------------------------------- checks

def check_ask(expect: dict, result: dict) -> list[str]:
    reasons = []
    answer = result.get("answer") or ""
    sources = [s.get("document_id") for s in result.get("sources") or []]
    whole = json.dumps({"answer": answer, "sources": result.get("sources")}, ensure_ascii=False)
    for term in expect.get("answer_contains", []):
        if term not in answer:
            reasons.append(f'a resposta não contém "{term}" (resposta: "{answer}")')
    for term in expect.get("answer_not_contains", []):
        if term in answer:
            reasons.append(f'a resposta contém "{term}" (resposta: "{answer}")')
    for term in expect.get("response_not_contains_ci", []):
        if term.lower() in whole.lower():
            reasons.append(f'a resposta contém "{term}" (resposta: "{answer}", fontes: {sources})')
    if "sources_equal" in expect and sorted(sources) != sorted(expect["sources_equal"]):
        reasons.append(f"fontes {sources}, esperado {expect['sources_equal']}")
    for doc in expect.get("sources_not_contains", []):
        if doc in sources:
            reasons.append(f'a resposta cita o documento "{doc}"')
    if "origin_in" in expect and result.get("origin") not in expect["origin_in"]:
        reasons.append(f"origin {result.get('origin')}, esperado {expect['origin_in']}")
    return reasons


def run_jaeger_check(step: dict, ctx: dict) -> list[str]:
    kind = step["check"]
    if kind == "no_pii":
        # A mesma consulta do roteiro do avaliador.
        status, payload = http("GET", f"{JAEGER}/api/traces?service={SERVICE}&limit=500")
        if status != 200:
            raise EvaluatorError(f"API do Jaeger respondeu {status}")
        raw = json.dumps(payload, ensure_ascii=False)
        reasons = []
        cpfs = len(CPF_RE.findall(raw))
        if cpfs:
            reasons.append(f"{cpfs} ocorrência(s) de CPF nos traces do Jaeger")
        for salary in SALARIES:
            n = raw.count(salary)
            if n:
                reasons.append(f'{n} ocorrência(s) do salário "{salary}" nos traces do Jaeger')
        return reasons

    trace = wait_trace(ctx["last_ask_us"], ctx.get("last_employee"))
    raw = json.dumps(trace, ensure_ascii=False)
    spans = trace["spans"]
    root = root_span(trace) or {}
    root_tags = tags_of(root)

    if kind == "content_policy":
        env = process_env(trace)
        reasons = []
        question, answer = ctx["last_question"], ctx["last_answer"]
        if env == "production":
            for label, needle in [("a pergunta", question), ("as mensagens enviadas ao modelo", "TASK: answer"),
                                  ("o system prompt", "Data e hora atual"), ("a resposta", answer)]:
                if needle and needle in raw:
                    reasons.append(f"em production o trace contém {label}")
        else:
            if "TASK: answer" not in raw:
                reasons.append(f"em {env} o trace não traz as mensagens enviadas ao modelo")
            if "Data e hora atual" not in raw:
                reasons.append(f"em {env} o trace não mostra a data e a hora enviadas ao modelo")
        return [f"[{env}] {r}" for r in reasons]

    if kind == "spans":
        reasons = []
        names = [s["operationName"] for s in spans]
        for attr in ("app.tenant_id", "app.origin"):
            if attr not in root_tags:
                reasons.append(f"o span raiz não tem {attr}")
        if not any(n.startswith("cache") for n in names):
            reasons.append(f"nenhum span de cache (spans: {names})")
        if not any(n.startswith("retrieval") for n in names):
            reasons.append(f"nenhum span de busca de trechos (spans: {names})")
        chat = [s for s in spans if tags_of(s).get("gen_ai.operation.name") == "chat"]
        if not chat:
            reasons.append(f"nenhum span de chamada ao modelo com gen_ai.operation.name=chat (spans: {names})")
        else:
            chat_tags = tags_of(chat[0])
            for attr in ("gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
                         "gen_ai.usage.cache_read.input_tokens"):
                if attr not in chat_tags:
                    reasons.append(f"o span da chamada ao modelo não tem {attr}")
        return reasons

    raise EvaluatorError(f"check de Jaeger desconhecido: {kind}")


# --------------------------------------------------------------------------- execução

def run_step(step: dict, ctx: dict, log: list) -> list[str]:
    action = step["action"]
    if action == "reset":
        for service in step.get("services", ["provider", "hr"]):
            url = {"provider": PROVIDER, "hr": HR}[service]
            status, _ = http("POST", f"{url}/admin/reset")
            if status != 200:
                raise EvaluatorError(f"reset do {service} respondeu {status}")
        return []

    if action == "pause":
        time.sleep(step.get("seconds", 1))
        return []

    if action == "ask":
        if step.get("trace"):
            time.sleep(2.0)  # separa este trace dos anteriores na janela de busca do Jaeger
            ctx["last_ask_us"] = int((time.time() - 1.0) * 1_000_000)
        status, result = http("POST", f"{EDGE}/ask", {"question": step["question"]}, auth(step["token"]))
        entry = {"action": "ask", "token": step["token"], "question": step["question"], "status": status}
        log.append(entry)
        if status != 200 or not isinstance(result, dict) or "answer" not in result:
            raise EvaluatorError(f"POST /ask ({step['token']}) respondeu {status}: {str(result)[:200]}")
        entry.update(origin=result.get("origin"), answer=result.get("answer"),
                     sources=[s.get("document_id") for s in result.get("sources") or []])
        ctx["last_question"], ctx["last_answer"] = step["question"], result.get("answer")
        ctx["last_employee"] = step.get("employee")
        return check_ask(step.get("expect", {}), result)

    if action in ("publish", "restore_kb"):
        doc = step["doc"] if action == "publish" else kb_document(step["tenant"], step["doc_id"])
        status, result = http("POST", f"{EDGE}/admin/documents", doc, auth(step["token"]))
        log.append({"action": action, "doc_id": doc["doc_id"], "status": status})
        allowed = step.get("allow_status", [200])
        if status not in allowed:
            raise EvaluatorError(f"POST /admin/documents ({doc['doc_id']}) respondeu {status}: {str(result)[:200]}")
        return []

    if action == "delete":
        status, result = http("DELETE", f"{EDGE}/admin/documents/{step['doc_id']}", None, auth(step["token"]))
        log.append({"action": "delete", "doc_id": step["doc_id"], "status": status})
        if status not in step.get("allow_status", [200]):
            raise EvaluatorError(f"DELETE /admin/documents/{step['doc_id']} respondeu {status}")
        return []

    if action == "hr_patch":
        url = f"{HR}/admin/tenants/{step['tenant']}/employees/{step['employee']}"
        status, _ = http("PATCH", url, step["body"])
        log.append({"action": "hr_patch", "employee": step["employee"], "status": status})
        if status != 200:
            raise EvaluatorError(f"PATCH no hr-fake respondeu {status}")
        return []

    if action == "mark_calls":
        ctx["answer_calls_mark"] = len(answer_calls())
        return []

    if action == "check_calls":
        calls = answer_calls()
        reasons = []
        if step.get("no_new_answer_calls"):
            new = len(calls) - ctx.get("answer_calls_mark", 0)
            if new > 0:
                reasons.append(f"{new} chamada(s) answer nova(s) em /admin/calls; esperado nenhuma")
        if "last_cached_tokens_gte" in step:
            if not calls:
                reasons.append("nenhuma chamada answer em /admin/calls")
            else:
                cached = calls[0].get("cached_tokens") or 0
                log.append({"action": "check_calls", "last_answer_cached_tokens": cached,
                            "last_answer_input_tokens": calls[0].get("input_tokens")})
                if cached < step["last_cached_tokens_gte"]:
                    reasons.append(f"a última chamada answer tem cached_tokens={cached}; "
                                   f"esperado >= {step['last_cached_tokens_gte']}")
        return reasons

    if action == "jaeger":
        return run_jaeger_check(step, ctx)

    raise EvaluatorError(f"ação desconhecida: {action}")


def run_item(item: dict) -> dict:
    started = time.time()
    ctx: dict = {}
    log: list = []
    reasons: list[str] = []
    status = "PASS"
    try:
        for step in item["steps"]:
            reasons += run_step(step, ctx, log)
    except EvaluatorError as exc:
        status, reasons = "ERROR", reasons + [f"avaliador não executou: {exc}"]
    except Exception as exc:  # erro no próprio avaliador também é ERROR
        status = "ERROR"
        reasons.append(f"erro no avaliador: {exc!r}")
        traceback.print_exc()
    for step in item.get("cleanup", []):
        try:
            run_step(step, ctx, log)
        except Exception as exc:
            status = "ERROR"
            reasons.append(f"limpeza falhou: {exc}")
    if status == "PASS" and reasons:
        status = "FAIL"
    return {
        "id": item["id"],
        "source": item["_source"],
        "incident": item.get("incident"),
        "attack": item.get("attack"),
        "description": item.get("description"),
        "status": status,
        "reasons": reasons,
        "steps": log,
        "duration_ms": round((time.time() - started) * 1000),
    }


def golden_to_item(raw: dict) -> dict:
    """Converte o formato do golden.jsonl (token/question/expected_sources/required_terms) em passos."""
    return {
        "id": raw["id"],
        "description": raw["question"],
        "steps": [{"action": "ask", "token": raw["token"], "question": raw["question"],
                   "expect": {"sources_equal": raw["expected_sources"],
                              "answer_contains": raw["required_terms"]}}],
    }


def load_items() -> list[dict]:
    items, seen = [], set()
    for name in SOURCES:
        path = EVALS / name
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            item = golden_to_item(raw) if "steps" not in raw else raw
            item["_source"] = name
            if item["id"] in seen:
                raise SystemExit(f"id duplicado na suíte: {item['id']} ({name}:{n})")
            seen.add(item["id"])
            items.append(item)
    return items


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def run_suite(output: Path | None = None, only: list[str] | None = None) -> dict:
    items = load_items()
    if only:
        items = [i for i in items if i["id"] in only]
    started = datetime.now(timezone.utc)
    # Estado inicial dos simulados; a aplicação é assumida recém-subida (docker compose down -v).
    try:
        http("POST", f"{PROVIDER}/admin/reset")
        http("POST", f"{HR}/admin/reset")
    except EvaluatorError as exc:
        print(f"aviso: reset inicial falhou: {exc}", file=sys.stderr)
    results = []
    for item in items:
        result = run_item(item)
        results.append(result)
        tag = f" ({result['incident']})" if result["incident"] else ""
        print(f"{result['status']:5} {result['id']}{tag}", flush=True)
        for reason in result["reasons"]:
            print(f"      {reason}", flush=True)
    summary = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "ERROR")}
    report = {
        "suite": "vereda-assistant",
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "summary": summary,
        "items": results,
    }
    if output is None:
        reports = EVALS / "reports"
        reports.mkdir(exist_ok=True)
        output = reports / f"report-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
        (reports / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
                                             newline="\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"\nsuíte: {summary['PASS']} PASS, {summary['FAIL']} FAIL, {summary['ERROR']} ERROR  -> {output}")
    report["_path"] = str(output)
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="caminho do relatório JSON")
    parser.add_argument("--only", nargs="*", help="roda só os ids indicados")
    args = parser.parse_args()
    report = run_suite(args.output, args.only)
    return 0 if report["summary"]["FAIL"] == 0 and report["summary"]["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
