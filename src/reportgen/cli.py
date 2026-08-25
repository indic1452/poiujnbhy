"""Командный интерфейс каркаса."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from . import domains
from .config import Settings
from .corpus import DOC_TYPES, load_corpus
from .facts import FactPack, FactPackError
from .llm import build_llm
from .pipeline import Outline, check_facts_coverage, generate_report
from .retrieval import BM25Index, Retriever
from .store.db import Database
from .store.models import DOC_STATUSES, ROLES
from .store.repo import Repositories
from .verify import blocking, summarize, verify_report


def _load_glossary(path: str | None) -> Dict[str, str] | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


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


# ------------------------------------------------- команды с базой данных ---

def _open_repos(args: argparse.Namespace) -> tuple[Repositories, Settings]:
    settings = _settings(args)
    settings.ensure_dirs()
    return Repositories(Database(settings.db_path)), settings


def _settings(args: argparse.Namespace) -> Settings:
    overrides = {}
    if getattr(args, "db", None):
        overrides["db_path"] = args.db
    return Settings.load(getattr(args, "config", None), **overrides)


def cmd_serve(args: argparse.Namespace) -> int:
    from .web.app import run  # noqa: PLC0415

    settings = _settings(args)
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    print(f"веб-интерфейс: http://{settings.host}:{settings.port}")
    print(f"база данных:   {settings.db_path}")
    print(f"модель:        {settings.llm_model} ({settings.llm_base_url})")
    run(settings)
    return 0


def cmd_useradd(args: argparse.Namespace) -> int:
    import getpass

    repos, _ = _open_repos(args)
    if repos.users.by_login(args.login) is not None:
        print(f"пользователь '{args.login}' уже существует", file=sys.stderr)
        return 1
    password = args.password or getpass.getpass("Пароль: ")
    if len(password) < 8:
        print("пароль короче 8 символов — так нельзя", file=sys.stderr)
        return 1
    user = repos.users.create(args.login, password, args.name or "", args.role)
    repos.audit.log("user.create", object_type="user", object_id=user.login,
                    details={"role": user.role})
    print(f"создан пользователь {user.login} с ролью {user.role}")
    return 0


def cmd_passwd(args: argparse.Namespace) -> int:
    import getpass

    repos, _ = _open_repos(args)
    user = repos.users.by_login(args.login)
    if user is None:
        print(f"пользователь '{args.login}' не найден", file=sys.stderr)
        return 1
    password = args.password or getpass.getpass("Новый пароль: ")
    if len(password) < 8:
        print("пароль короче 8 символов — так нельзя", file=sys.stderr)
        return 1
    repos.users.set_password(user.id, password)
    repos.sessions.delete_for_user(user.id)
    print(f"пароль для {user.login} изменён, активные сессии закрыты")
    return 0


def cmd_users(args: argparse.Namespace) -> int:
    repos, _ = _open_repos(args)
    users = repos.users.list_all()
    if not users:
        print("пользователей нет — создайте администратора: reportgen useradd --login admin --role admin")
        return 1
    for user in users:
        state = "активен" if user.active else "заблокирован"
        print(f"{user.login:20} {user.role:10} {state:12} {user.full_name}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    try:
        from .ingest.pipeline import ingest_directory, ingest_path  # noqa: PLC0415
    except ImportError as error:
        print(f"модуль приёма документов недоступен: {error}", file=sys.stderr)
        return 2

    repos, settings = _open_repos(args)
    target = Path(args.path) if args.path else Path(settings.library_dir)
    if not target.exists():
        print(f"путь не найден: {target}", file=sys.stderr)
        return 1

    doc_type = getattr(args, "doc_type", None)
    if doc_type is not None and doc_type not in DOC_TYPES:
        print(f"неизвестный тип документа '{doc_type}'; доступны: "
              f"{', '.join(DOC_TYPES)}", file=sys.stderr)
        return 1

    domain = getattr(args, "domain", None)
    if domain:
        known = domains.registry(settings.domains_path).ids
        if known and domain not in known:
            print(f"неизвестное направление '{domain}'; доступны: {', '.join(known)}",
                  file=sys.stderr)
            return 1

    if target.is_dir():
        result = ingest_directory(repos, target, force=args.force, progress=print,
                                  doc_type=doc_type, domain=domain,
                                  domains_path=settings.domains_path,
                                  jobs=getattr(args, "jobs", 0) or None)
    else:
        result = ingest_path(repos, target, root=target.parent, force=args.force,
                             doc_type=doc_type, domain=domain,
                             domains_path=settings.domains_path)
    print(result.summary() if hasattr(result, "summary") else result)

    # Из пачки в пятьсот файлов три не разобрались — и раньше об этом
    # говорила одна цифра в итоговой строке. КАКИЕ именно и что с ними не
    # так, знал только список предупреждений, который не печатался нигде.
    # Инженер не мог ни починить, ни даже узнать, что чинить.
    warnings = list(getattr(result, "warnings", []) or [])
    if warnings:
        print(f"\nЗамечания ({len(warnings)}):", file=sys.stderr)
        for warning in warnings:
            print(f"  {warning}", file=sys.stderr)

    failed = int(getattr(result, "failed", 0) or 0)
    if failed:
        # Ненулевой код нужен скриптам: load-library.ps1 обязан остановиться и
        # сказать, что часть библиотеки не принята, а не рапортовать успех.
        print(f"\nНе принято файлов: {failed}", file=sys.stderr)
        return 3
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    repos, _ = _open_repos(args)
    documents = repos.documents.list(args.doc_type, getattr(args, "domain", None))
    if not documents:
        print("библиотека пуста")
        return 1
    for document in documents:
        domain = document.domain or "—"
        mark = "" if document.status == "current" else f"  [{document.status}]"
        print(f"{document.doc_type:12} {domain:12} {document.chunk_count:5} чанков  "
              f"{document.doc_id}{mark}")
    by_domain = repos.documents.domains()
    print("по направлениям: " + ", ".join(f"{name} {count}" for name, count in by_domain.items()))
    stats = repos.documents.stats()
    total = sum(item["chunks"] for item in stats.values())
    print(f"итого документов {len(documents)}, чанков {total}, векторов {repos.vectors.count()}")
    return 0


def cmd_formats(args: argparse.Namespace) -> int:
    """Что система умеет читать прямо сейчас и чего для остального не хватает."""
    from .ingest.convert import format_support  # noqa: PLC0415

    specs = format_support()
    ready = [spec for spec in specs if spec["available"]]
    blocked = [spec for spec in specs if not spec["available"]]

    print("Доступные форматы:")
    for spec in ready:
        print(f"  {' '.join(spec['suffixes']):32} {spec['name']:12} {spec['note']}")
    if blocked:
        print("\nНедоступны — не хватает инструментов:")
        for spec in blocked:
            missing = ", ".join(
                f"{item['name']} ({item['hint']})"
                for item in spec["requires"] if not item["available"]
            )
            print(f"  {' '.join(spec['suffixes']):32} {spec['name']:12} {missing}")
    total = sum(len(spec["suffixes"]) for spec in ready)
    print(f"\nИтого расширений: {total} доступно, "
          f"{sum(len(spec['suffixes']) for spec in blocked)} требуют установки")
    return 0 if ready else 1


def cmd_doc_status(args: argparse.Namespace) -> int:
    repos, _ = _open_repos(args)
    if repos.documents.by_doc_id(args.doc_id) is None:
        print(f"документ не найден: {args.doc_id}", file=sys.stderr)
        return 1
    repos.documents.set_status(args.doc_id, args.status, args.superseded_by or "")
    repos.audit.log("library.status", object_type="document", object_id=args.doc_id,
                    details={"status": args.status})
    print(f"{args.doc_id}: статус «{args.status}»"
          + (f", заменён на {args.superseded_by}" if args.superseded_by else ""))
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    try:
        from .embeddings import EmbeddingClient, index_embeddings  # noqa: PLC0415
    except ImportError as error:
        print(f"модуль эмбеддингов недоступен: {error}", file=sys.stderr)
        return 2

    repos, settings = _open_repos(args)
    client = EmbeddingClient(
        base_url=settings.embed_base_url,
        model=settings.embed_model,
        api_key=settings.embed_api_key,
        timeout=settings.embed_timeout,
    )
    count = index_embeddings(repos, client, only_missing=not args.force, progress=print)
    print(f"векторов проставлено: {count}, всего в базе: {repos.vectors.count()}")
    return 0


def cmd_dataset(args: argparse.Namespace) -> int:
    try:
        from .dataset import export_dataset  # noqa: PLC0415
    except ImportError as error:
        print(f"модуль датасета недоступен: {error}", file=sys.stderr)
        return 2

    repos, _ = _open_repos(args)
    manifest = export_dataset(repos, Path(args.out), kind=args.kind)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    try:
        from .evaluate import load_golden_set, run_eval  # noqa: PLC0415
    except ImportError as error:
        print(f"модуль оценки недоступен: {error}", file=sys.stderr)
        return 2

    settings = _settings(args)
    llm_kwargs = {} if args.llm == "stub" else {
        "base_url": args.base_url or settings.llm_base_url,
        "model": args.model or settings.llm_model,
    }
    llm = build_llm(args.llm, **llm_kwargs)
    retriever = Retriever(BM25Index.load(args.index)) if args.index else None
    cases = load_golden_set(args.golden)
    report = run_eval(cases, llm, Path(args.outlines or settings.templates_dir),
                      retriever=retriever, glossary=_load_glossary(args.glossary))
    text = report.to_markdown()
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        Path(args.out).with_suffix(".json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"результаты записаны: {args.out}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .web.service import ReportService  # noqa: PLC0415

    repos, settings = _open_repos(args)
    service = ReportService(repos=repos, settings=settings)
    print(json.dumps(service.stats(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reportgen",
        description="Каркас конвейера генерации технических отчётов",
    )
    parser.add_argument("--config", default=None, help="путь к JSON-файлу настроек")
    parser.add_argument("--db", default=None, help="путь к базе данных (перекрывает настройки)")
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

    # -- работа с установленной системой (база данных, веб) --------------

    p_serve = sub.add_parser("serve", help="запустить веб-интерфейс")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=cmd_serve)

    p_useradd = sub.add_parser("useradd", help="создать пользователя")
    p_useradd.add_argument("--login", required=True)
    p_useradd.add_argument("--name", default="")
    p_useradd.add_argument("--role", default="engineer", choices=list(ROLES))
    p_useradd.add_argument("--password", default=None, help="если не задан — будет запрошен")
    p_useradd.set_defaults(func=cmd_useradd)

    p_passwd = sub.add_parser("passwd", help="сменить пароль пользователя")
    p_passwd.add_argument("--login", required=True)
    p_passwd.add_argument("--password", default=None)
    p_passwd.set_defaults(func=cmd_passwd)

    p_users = sub.add_parser("users", help="список пользователей")
    p_users.set_defaults(func=cmd_users)

    p_ingest = sub.add_parser("ingest", help="загрузить документы библиотеки в базу")
    p_ingest.add_argument("path", nargs="?", default=None, help="файл или каталог")
    p_ingest.add_argument("--force", action="store_true", help="переиндексировать даже без изменений")
    p_ingest.add_argument(
        "--doc-type", default=None,
        help="тип для всех файлов: literature, standards, datasheets, reports, regulations. "
             "Без него тип берётся из имени каталога верхнего уровня",
    )
    p_ingest.add_argument(
        "--jobs", type=int, default=0,
        help="сколько файлов разбирать одновременно (0 — по числу ядер минус одно)",
    )
    p_ingest.add_argument(
        "--domain", default=None,
        help="направление техники для всех файлов (satellite, microwave, protocols …). "
             "Без него определяется по тексту",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_lib = sub.add_parser("library", help="что лежит в библиотеке")
    p_lib.add_argument("--doc-type", default=None)
    p_lib.add_argument("--domain", default=None, help="фильтр по направлению техники")
    p_lib.set_defaults(func=cmd_library)

    p_formats = sub.add_parser("formats", help="какие форматы документов система умеет читать")
    p_formats.set_defaults(func=cmd_formats)

    p_status = sub.add_parser("doc-status", help="отметить актуальность документа библиотеки")
    p_status.add_argument("--doc-id", required=True)
    p_status.add_argument("--status", required=True, choices=list(DOC_STATUSES))
    p_status.add_argument("--superseded-by", default=None, help="doc_id новой редакции")
    p_status.set_defaults(func=cmd_doc_status)

    p_embed = sub.add_parser("embed", help="построить векторы для плотного поиска")
    p_embed.add_argument("--force", action="store_true")
    p_embed.set_defaults(func=cmd_embed)

    p_dataset = sub.add_parser("dataset", help="выгрузить обучающий набор из правок инженеров")
    p_dataset.add_argument("--out", required=True)
    p_dataset.add_argument("--kind", default="sft", choices=["sft", "dpo"])
    p_dataset.set_defaults(func=cmd_dataset)

    p_eval = sub.add_parser("eval", help="прогнать золотой набор и посчитать метрики")
    p_eval.add_argument("--golden", required=True, help="JSON-манифест золотого набора")
    p_eval.add_argument("--outlines", default=None)
    p_eval.add_argument("--index", default=None)
    p_eval.add_argument("--glossary", default=None)
    p_eval.add_argument("--llm", default="stub", choices=["stub", "openai"])
    p_eval.add_argument("--base-url", default=None)
    p_eval.add_argument("--model", default=None)
    p_eval.add_argument("--out", default=None)
    p_eval.set_defaults(func=cmd_eval)

    p_stats = sub.add_parser("stats", help="метрики установки")
    p_stats.set_defaults(func=cmd_stats)

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
