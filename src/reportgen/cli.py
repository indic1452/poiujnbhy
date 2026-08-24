"""Командный интерфейс каркаса."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from .corpus import load_corpus
from .facts import FactPack, FactPackError
from .llm import build_llm
from .pipeline import Outline, check_facts_coverage, generate_report
from .retrieval import BM25Index, Retriever
from .verify import blocking, summarize, verify_report


def _load_glossary(path: str | None) -> Dict[str, str] | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_index(args: argparse.Namespace) -> int:
    chunks = load_corpus(args.corpus)
    if not chunks:
        print(f"в каталоге {args.corpus} не найдено ни одного документа", file=sys.stderr)
        return 1
    index = BM25Index(chunks)
    index.save(args.out)
    by_type: Dict[str, int] = {}
    for chunk in chunks:
        by_type[chunk.doc_type] = by_type.get(chunk.doc_type, 0) + 1
    print(f"проиндексировано чанков: {len(chunks)}")
    for doc_type, count in sorted(by_type.items()):
        print(f"  {doc_type}: {count}")
    print(f"индекс сохранён: {args.out}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    retriever = Retriever(BM25Index.load(args.index))
    hits = retriever.search(args.query, top_k=args.top_k, doc_types=args.doc_types or None)
    if not hits:
        print("ничего не найдено")
        return 1
    for hit in hits:
        preview = " ".join(hit.chunk.text.split())[:160]
        print(f"[{hit.rank}] {hit.score:6.3f}  {hit.chunk.citation}\n      {preview}…")
    return 0


def cmd_check_facts(args: argparse.Namespace) -> int:
    facts = FactPack.load(args.facts)
    outline = Outline.load(args.outline)
    missing = check_facts_coverage(facts, outline)
    if not missing:
        print("все обязательные измерения на месте")
        return 0
    print("не хватает измерений (доснимите до генерации отчёта):")
    for section_id, keys in missing.items():
        print(f"  {section_id}: {', '.join(keys)}")
    return 1


def cmd_generate(args: argparse.Namespace) -> int:
    facts = FactPack.load(args.facts)
    outline = Outline.load(args.outline)
    retriever = Retriever(BM25Index.load(args.index)) if args.index else None

    llm_kwargs = {}
    if args.llm != "stub":
        llm_kwargs = {"base_url": args.base_url, "model": args.model}
    llm = build_llm(args.llm, **llm_kwargs)

    result = generate_report(
        facts, outline, llm, retriever,
        top_k=args.top_k,
        generated_at=args.generated_at,
        index_version=Path(args.index).name if args.index else "—",
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.markdown, encoding="utf-8")
    print(f"отчёт записан: {out} ({len(result.markdown.split())} слов)")

    if result.missing_facts:
        print("внимание, отсутствуют измерения: " + ", ".join(result.missing_facts))

    issues = verify_report(result.markdown, facts, outline, glossary=_load_glossary(args.glossary))
    sidecar = out.with_suffix(".meta.json")
    sidecar.write_text(
        json.dumps(
            {
                "meta": result.meta,
                "missing_facts": result.missing_facts,
                "sources": [chunk.chunk_id for chunk in result.registry.chunks],
                "issues": [
                    {"level": i.level, "code": i.code, "section": i.section, "message": i.message}
                    for i in issues
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _print_issues(issues)
    print(f"метаданные сборки: {sidecar}")
    return 1 if blocking(issues) else 0


def cmd_verify(args: argparse.Namespace) -> int:
    facts = FactPack.load(args.facts)
    outline = Outline.load(args.outline) if args.outline else None
    markdown = Path(args.report).read_text(encoding="utf-8")
    issues = verify_report(markdown, facts, outline, glossary=_load_glossary(args.glossary))
    _print_issues(issues)
    return 1 if blocking(issues) else 0


def _print_issues(issues: List) -> None:
    counts = summarize(issues)
    if not issues:
        print("проверка пройдена: замечаний нет")
        return
    for issue in issues:
        print(issue)
    print(
        f"итого: ошибок {counts.get('error', 0)}, "
        f"предупреждений {counts.get('warning', 0)}"
    )
    if counts.get("error"):
        print("ЭКСПОРТ ЗАБЛОКИРОВАН: устраните ошибки")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reportgen",
        description="Каркас конвейера генерации технических отчётов",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="построить индекс по корпусу")
    p_index.add_argument("--corpus", required=True)
    p_index.add_argument("--out", required=True)
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="проверить поиск по индексу")
    p_search.add_argument("--index", required=True)
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--doc-types", nargs="*", default=None)
    p_search.set_defaults(func=cmd_search)

    p_check = sub.add_parser("check-facts", help="проверить полноту фактов до генерации")
    p_check.add_argument("--facts", required=True)
    p_check.add_argument("--outline", required=True)
    p_check.set_defaults(func=cmd_check_facts)

    p_gen = sub.add_parser("generate", help="сгенерировать отчёт")
    p_gen.add_argument("--facts", required=True)
    p_gen.add_argument("--outline", required=True)
    p_gen.add_argument("--index", default=None)
    p_gen.add_argument("--out", required=True)
    p_gen.add_argument("--llm", default="stub", choices=["stub", "openai", "llamacpp", "vllm", "ollama"])
    p_gen.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    p_gen.add_argument("--model", default="local-model")
    p_gen.add_argument("--top-k", type=int, default=6)
    p_gen.add_argument("--glossary", default=None)
    p_gen.add_argument("--generated-at", default=None, help="дата сборки (для воспроизводимости)")
    p_gen.set_defaults(func=cmd_generate)

    p_verify = sub.add_parser("verify", help="проверить готовый отчёт")
    p_verify.add_argument("--facts", required=True)
    p_verify.add_argument("--report", required=True)
    p_verify.add_argument("--outline", default=None)
    p_verify.add_argument("--glossary", default=None)
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FactPackError, FileNotFoundError, ValueError) as error:
        print(f"ошибка: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
