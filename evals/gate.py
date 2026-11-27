"""Gate de qualidade: roda a suíte e compara cada item com o mesmo item na baseline (ADR 0006).

Uso:
    python evals/gate.py
    python evals/gate.py --report evals/reports/latest.json    # compara um relatório já gerado

Reprova (código de saída 1) quando:
  - um avaliador não conseguiu executar (ERROR): avaliador quebrado nunca conta como aprovado;
  - um item de incidente ou um ataque falha agora;
  - um item que passava na baseline falha agora (regressão);
  - um item novo (sem estado na baseline) falha agora;
  - um item da baseline sumiu da suíte.
A baseline (evals/baselines/v1-baseline.json) é só lida: nunca é regenerada pelo gate.
Código de saída 2: o gate em si não conseguiu rodar (baseline ausente ou ilegível).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import suite  # noqa: E402

BASELINE = suite.EVALS / "baselines" / "v1-baseline.json"


def judge(item: dict | None, base: dict | None) -> tuple[bool, str | None]:
    """(aprovado, motivo da reprovação) de um item, comparado à baseline."""
    if item is None:
        return False, "item da baseline não existe mais na suíte"
    now = item["status"]
    critical = item.get("incident") is not None
    if now == "ERROR":
        return False, "avaliador não conseguiu executar"
    if now == "PASS":
        return True, None
    if item.get("attack"):
        return False, "ataque bem-sucedido"
    if critical:
        return False, "item de incidente falhando"
    if base is None:
        return False, "item novo (sem estado na baseline) falhando"
    if base["status"] == "PASS":
        return False, "regressão: passava na baseline"
    return False, "falhando (já falhava na baseline)"


def compare(report: dict, baseline: dict) -> int:
    base_items = {i["id"]: i for i in baseline["items"]}
    now_items = {i["id"]: i for i in report["items"]}
    ids = list(now_items) + [i for i in base_items if i not in now_items]
    width = max(len(f"{i} ({now_items.get(i, base_items.get(i, {})).get('incident')})") for i in ids) + 2

    failed: dict[str, int] = {}
    lines = []
    for item_id in ids:
        item, base = now_items.get(item_id), base_items.get(item_id)
        ok, why = judge(item, base)
        ref = item or base
        label = f"{item_id} ({ref['incident']})" if ref.get("incident") else item_id
        base_state = base["status"] if base else "NOVO"
        now_state = item["status"] if item else "AUSENTE"
        lines.append(f"{'PASS' if ok else 'FAIL'} {label:<{width}} baseline: {base_state:<5} agora: {now_state}")
        if not ok:
            failed[why] = failed.get(why, 0) + 1
            lines.append(f"     motivo: {why}")
            for reason in (item or {}).get("reasons", [])[:4]:
                lines.append(f"       - {reason}")
    print("\n".join(lines))
    print()
    if failed:
        detail = ", ".join(f"{n} {why}" for why, n in failed.items())
        print(f"gate: REPROVADO ({sum(failed.values())} itens: {detail})")
        return 1
    print(f"gate: APROVADO ({len(now_items)} itens; baseline {baseline.get('git_sha', '')[:7]})")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--report", type=Path, help="compara um relatório existente em vez de rodar a suíte")
    args = parser.parse_args()
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"gate: ERRO — baseline ilegível ({args.baseline}): {exc}")
        return 2
    if args.report:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    else:
        print("== suíte ==")
        report = suite.run_suite()
    print(f"\n== gate: comparação item a item com {args.baseline.as_posix()} ==")
    return compare(report, baseline)


if __name__ == "__main__":
    sys.exit(main())
