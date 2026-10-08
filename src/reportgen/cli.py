"""Командный интерфейс каркаса."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import domains
from .config import Settings, settings_warnings
from .corpus import DOC_TYPES, load_corpus
from .facts import FactPack, FactPackError
from .llm import build_llm
from .pipeline import Outline, check_facts_coverage, generate_report
from .retrieval import BM25Index, Retriever
from .store.db import Database, console_work
from .store.models import DOC_STATUSES, ROLE_TITLES, ROLES, role_title_of
from .store.repo import Repositories
from .verify import blocking, summarize, verify_report


def _load_glossary(path: str | None) -> dict[str, str] | None:
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
    by_type: dict[str, int] = {}
    for chunk in chunks:
        by_type[chunk.doc_type] = by_type.get(chunk.doc_type, 0) + 1
    print(f"проиндексировано чанков: {len(chunks)}")
    for doc_type, count in sorted(by_type.items()):
        print(f"  {doc_type}: {count}")
    print(f"индекс сохранён: {args.out}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    settings = _settings(args)
    retriever = Retriever(BM25Index.load(args.index), terms_path=settings.terms_path)
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
    settings = _settings(args)
    facts = FactPack.load(args.facts)
    outline = Outline.load(args.outline)
    retriever = Retriever(BM25Index.load(args.index), terms_path=settings.terms_path) if args.index else None

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


def _print_issues(issues: list) -> None:
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
    # Схема — из настроек. Печатать http://, когда включён https, значит
    # послать человека по адресу, который у него не откроется.
    схема = "https" if getattr(settings, "https", False) else "http"
    print(f"веб-интерфейс: {схема}://{settings.host}:{settings.port}")
    print(f"база данных:   {settings.db_path}")
    print(f"модель:        {settings.llm_model} ({settings.llm_base_url})")
    # Настройки, от которых система не падает, а работает вполсилы. На
    # изолированной машине спросить некого — говорим сами, в глаза.
    for trouble in settings_warnings(settings):
        print(f"ВНИМАНИЕ:      {trouble}", file=sys.stderr)
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
    user = repos.users.create(args.login, password, args.name or "", args.role,
                              department=args.department or "", team=args.team or "")
    repos.audit.log("user.create", object_type="user", object_id=user.login,
                    details={"role": user.role})
    print(f"создан военнослужащий {user.login} — {role_title_of(user.role)}")
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
        print("военнослужащих нет — заведите создателя системы: "
              "reportgen useradd --login admin --role owner")
        return 1
    for user in users:
        state = "работает" if user.active else "отключён"
        title = role_title_of(user.role)
        print(f"{user.login:20} {title:30} {state:10} {user.full_name}")
    return 0


def _id_root(target: Path, library: Path) -> Path:
    """От чего считать идентификатор документа при приёме одного файла.

    От корня библиотеки, если файл внутри неё: тогда приём одного документа
    («reportgen ingest ...\\library\\standards\\новый-гост.pdf») и полный
    проход дадут одну и ту же запись, а не две с разными идентификаторами.
    Файл со стороны считаем от его каталога, как и раньше.
    """
    try:
        if target.resolve().is_relative_to(library.resolve()):
            return library
    except (OSError, ValueError):
        pass
    return target.parent


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

    # Загрузка с нуля: сначала стираем всё, что было. Иначе документы,
    # удалённые с диска, остаются в базе, а изменившиеся правила нарезки
    # применяются только к новым файлам.
    if getattr(args, "reset", False):
        removed = repos.documents.clear_all()
        print(f"библиотека очищена: удалено документов {removed}")

    # Корень библиотеки — то, от чего считается идентификатор документа.
    # Когда принимают одну папку внутри библиотеки (штатная догрузка пачки:
    # перебирать тринадцать тысяч файлов ради полусотни новых незачем),
    # идентификатор обязан выйти тот же, что и при полном проходе. Иначе
    # следующая полная загрузка заведёт те же файлы вторым комплектом
    # записей, и в выдачу пойдут парные фрагменты. Веб-приём так и делал
    # всегда; консольный считал от указанного каталога — и расходился с ним.
    library = Path(settings.library_dir)

    if target.is_dir():
        # Пока идёт приём, фоновый построитель векторов в приложении стоит:
        # писать в SQLite можно только по одному, и вдвоём они дают
        # «database is locked» обоим. Отмечаемся на каждом файле, иначе
        # многочасовая загрузка через четверть часа сочлась бы брошенной.
        with console_work(repos.db.path, "приём библиотеки") as отметиться:
            def ход(сообщение) -> None:
                отметиться()
                print(сообщение)

            result = ingest_directory(repos, target, base=library, force=args.force,
                                      progress=ход,
                                      doc_type=doc_type, domain=domain,
                                      domains_path=settings.domains_path,
                                      jobs=getattr(args, "jobs", 0) or None)
    else:
        result = ingest_path(repos, target, root=_id_root(target, library),
                             force=args.force,
                             doc_type=doc_type, domain=domain,
                             domains_path=settings.domains_path)
    print(result.summary() if hasattr(result, "summary") else result)

    # Из пачки в пятьсот файлов три не разобрались — и раньше об этом
    # говорила одна цифра в итоговой строке. КАКИЕ именно и что с ними не
    # так, знал только список предупреждений, который не печатался нигде.
    # Инженер не мог ни починить, ни даже узнать, что чинить.
    # Отказы и замечания печатаются РАЗДЕЛЬНО. В одном списке «файл пуст»
    # (документа не будет) стоял вперемешку с «формат не читается» (так и
    # задумано), да ещё отсортированный по алфавиту вместе с ним: даже
    # добравшись до списка, инженер не мог отличить строки, требующие
    # действий, от справочных.
    failures = list(getattr(result, "failures", []) or [])
    notes = list(getattr(result, "notes", []) or [])
    if not failures and not notes:
        notes = list(getattr(result, "warnings", []) or [])

    if failures:
        print(f"\nНЕ ПРИНЯТО, требует действий ({len(failures)}):", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
    if notes:
        print(f"\nЗамечания, к сведению ({len(notes)}):", file=sys.stderr)
        for line in notes:
            print(f"  {line}", file=sys.stderr)

    failed = int(getattr(result, "failed", 0) or 0)
    if failed:
        # Ненулевой код нужен скриптам: load-library.ps1 обязан сказать, что
        # часть библиотеки не принята, а не рапортовать успех.
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
    vectors = repos.vectors.count()
    print(f"итого документов {len(documents)}, чанков {total}, векторов {vectors}")
    # «Векторов 2000» при 5000 фрагментов выглядит благополучно, а на деле три
    # пятых библиотеки в смысловом поиске не участвуют — обычный итог упавшей
    # службы эмбеддингов посреди большой пачки.
    if total and vectors < total:
        print(f"ВНИМАНИЕ: без векторов фрагментов {total - vectors} — "
              "они находятся только словесным поиском.", file=sys.stderr)
        print("Поднимите комплекс (.\\start-all.ps1) и выполните "
              "«reportgen embed» ещё раз.", file=sys.stderr)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Какие документы помощник не увидит и почему — по всей библиотеке.

    Отдел: «он не находит и вообще не ссылается на этот документ, и так со
    многим — нужно, чтобы не было пропусков по всей библиотеке». Разбирать
    по одному, когда их тридцать тысяч, бессмысленно: беда у ненаходимого
    документа тихая, он числится в списке, а в ответ не попадает никогда.

    Ничего не меняется: это диагностика. Файлы не читаются, всё берётся из
    описи и фрагментов — на тридцати тысячах это минуты.
    """
    from .audit import audit_library, summarize  # noqa: PLC0415

    repos, _ = _open_repos(args)
    всего = len(repos.documents.list(getattr(args, "doc_type", None),
                                     getattr(args, "domain", None)))
    if not всего:
        print("библиотека пуста")
        return 1
    отчёты = audit_library(repos, doc_type=getattr(args, "doc_type", None),
                           domain=getattr(args, "domain", None))
    только = str(getattr(args, "code", "") or "")
    if только:
        отчёты = [отчёт for отчёт in отчёты if только in отчёт.codes]
    фрагментов = int(repos.db.scalar("SELECT count(*) FROM chunks") or 0)
    for строка in summarize(отчёты, total=всего, vectors=repos.vectors.count(),
                            chunks=фрагментов):
        print(строка)

    if not отчёты:
        print("\nПропусков нет: каждый документ находится и словом, и смыслом.")
        return 0

    предел = int(getattr(args, "limit", 0) or 0)
    показать = отчёты if предел <= 0 else отчёты[:предел]
    for отчёт in показать:
        метка = "НЕ НАХОДИТСЯ" if отчёт.blocked else "замечание"
        print(f"\n[{метка}] {отчёт.doc_id}")
        print(f"    название: {отчёт.title or '—'}")
        print(f"    фрагментов {отчёт.chunks}, знаков {отчёт.chars}, "
              f"векторов {отчёт.vectors}")
        for беда in отчёт.findings:
            print(f"    · {беда.problem}")
            print(f"      → {беда.advice}")
    if предел > 0 and len(отчёты) > предел:
        print(f"\n…и ещё {len(отчёты) - предел}. Весь список: --limit 0")
    # Ненаходимый документ — это не «замечание к оформлению», а дыра в
    # библиотеке: код возврата должен это отражать, чтобы проверку можно
    # было поставить в скрипт приёма.
    return 1 if any(отчёт.blocked for отчёт in отчёты) else 0


def cmd_why(args: argparse.Namespace) -> int:
    """Почему этот документ не выходит по этому вопросу.

    Продолжение сплошной проверки для случая «вот с этим разберитесь
    поимённо». Показывает три вещи по порядку: что с самим документом, какие
    слова вопроса вообще есть в его тексте, и на каком месте он оказался в
    настоящей выдаче поиска — или кто его оттуда вытеснил.
    """
    from .audit import audit_library  # noqa: PLC0415
    from .designations import designations_in  # noqa: PLC0415
    from .retrieval import tokenize  # noqa: PLC0415

    repos, settings = _open_repos(args)
    документы = repos.documents.list(query=args.doc, limit=10)
    if not документы:
        print(f"в описи нет документа по запросу «{args.doc}»", file=sys.stderr)
        print("Поищите по части имени файла или по названию: reportgen library",
              file=sys.stderr)
        return 1
    if len(документы) > 1:
        print(f"под «{args.doc}» подходит {len(документы)} документов, "
              f"беру первый. Остальные:")
        for документ in документы[1:]:
            print(f"  {документ.doc_id} — {документ.title}")
    документ = документы[0]

    print(f"\nДОКУМЕНТ: {документ.doc_id}")
    print(f"  название:    {документ.title or '—'}")
    print(f"  тип:         {документ.doc_type}, направление {документ.domain or '—'}")
    print(f"  статус:      {документ.status}")
    print(f"  фрагментов:  {документ.chunk_count}")

    беды = [отчёт for отчёт in audit_library(repos)
            if отчёт.doc_id == документ.doc_id]
    if беды:
        print("\nЗАМЕЧАНИЯ:")
        for беда in беды[0].findings:
            print(f"  · {беда.problem}")
            print(f"    → {беда.advice}")
    else:
        print("\nС документом всё в порядке: он находится и словом, и смыслом.")

    if not args.query:
        return 0

    print(f"\nВОПРОС: {args.query}")
    слова = tokenize(args.query)
    есть, нет = [], []
    for слово in dict.fromkeys(слова):
        строки = repos.db.query(
            "SELECT 1 FROM chunks WHERE document_id = ? AND lower(text) LIKE ? "
            "LIMIT 1", (документ.id, f"%{слово}%"))
        (есть if строки else нет).append(слово)
    print(f"  слов вопроса в тексте документа: {len(есть)} из {len(есть) + len(нет)}")
    if есть:
        print("    есть: " + ", ".join(есть))
    if нет:
        print("    нет:  " + ", ".join(нет))
    обозначения = designations_in(args.query)
    if обозначения:
        print("  обозначения в вопросе: " + ", ".join(обозначения)
              + " — такой документ помощник подкладывает по имени, "
                "даже если поиск его не принёс")

    retriever = _retriever(repos, settings)
    if retriever is None:
        print("\nПоиск не собран: проверить выдачу отсюда нельзя.")
        return 0
    попадания = retriever.search(args.query, top_k=int(args.top_k))
    место = next((n for n, hit in enumerate(попадания, 1)
                  if hit.chunk.doc_id == документ.doc_id), 0)
    print(f"\nВЫДАЧА ПОИСКА (первые {len(попадания)}):")
    for n, hit in enumerate(попадания, 1):
        свой = " <<< наш документ" if hit.chunk.doc_id == документ.doc_id else ""
        print(f"  {n:2}. {hit.score:7.3f}  {hit.chunk.breadcrumbs}{свой}")
    if место:
        print(f"\nДокумент найден, место {место}.")
        return 0
    print("\nДокумент в выдачу НЕ ПОПАЛ. Смотрите замечания выше: чаще всего "
          "причина в том, что слов вопроса в его тексте нет вовсе.")
    return 1


def _retriever(repos: Repositories, settings: Settings):
    """Поисковик как в веб-приложении. Не собрался — работаем без него."""
    try:
        from .search import DatabaseRetriever  # noqa: PLC0415

        return DatabaseRetriever(repos, terms_path=settings.terms_path)
    except Exception as ошибка:              # noqa: BLE001 — диагностика
        print(f"поиск не собран: {ошибка}", file=sys.stderr)
        return None


def cmd_retitle(args: argparse.Namespace) -> int:
    """Переписать негодные названия документов ПРЯМО В БАЗЕ, не трогая файлы.

    Починка приёма не исправляет того, что уже принято, а перечитывать
    тридцать тысяч файлов — несколько суток работы. Между тем всё нужное
    уже лежит в базе: у документа есть исходный путь, а имя файла человек
    выбирал сам и оно почти всегда лучше служебного заголовка из середины
    документа. Значит, названия можно пересобрать за минуты.

    По умолчанию НИЧЕГО НЕ МЕНЯЕТСЯ: печатается, что изменилось бы. Второго
    шанса у библиотеки нет, поэтому применение — отдельным ключом --apply.
    """
    from .ingest import titles as _titles

    repos, _ = _open_repos(args)
    documents = repos.documents.list(getattr(args, "doc_type", None),
                                     getattr(args, "domain", None))
    if not documents:
        print("библиотека пуста")
        return 1

    предел = int(getattr(args, "limit", 0) or 0)
    правки: list[tuple[str, str, str, str]] = []
    целых = 0
    безнадёжных: list[tuple[str, str, str]] = []

    # Бланки видны только на всей библиотеке сразу: по одному названию не
    # понять, что оно пришло не от документа, а из свойств файла. Зато
    # пятьсот разных документов не могут честно называться одинаково.
    повторы = _titles.repeated_titles(str(документ.title or "")
                                      for документ in documents)
    из_бланка = 0

    for document in documents:
        было = str(document.title or "")
        имя = Path(str(document.source_path or document.doc_id)).name
        бланк = повторы.get(_titles.normalize_title(было), 0)
        кандидаты = [] if бланк else [было]
        стало, откуда, отвергнуто = _titles.choose_title(кандидаты, filename=имя)
        if not стало or стало == было:
            причина = (f"одно название на {бланк} документов — пришло из бланка"
                       if бланк else _titles.title_problem(было))
            if причина is None:
                целых += 1
            else:
                безнадёжных.append((document.doc_id, было, причина))
            continue
        if бланк:
            откуда = f"{откуда}; прежнее носили {бланк} документов"
            из_бланка += 1
        правки.append((document.doc_id, было, стало, откуда))

    print(f"документов в библиотеке: {len(documents)}")
    print(f"названия в порядке:      {целых}")
    print(f"будет переименовано:     {len(правки)}")
    if из_бланка:
        print(f"из них с названием бланка: {из_бланка} "
              f"(одно название на {_titles.MIN_TEMPLATE_COPIES} и более документов)")
    if безнадёжных:
        print(f"негодных, но заменить нечем: {len(безнадёжных)} "
              f"(ни в документе, ни в имени файла нет пригодного названия)")

    показано = правки if предел <= 0 else правки[:предел]
    for doc_id, было, стало, откуда in показано:
        print(f"\n  {doc_id}")
        print(f"    было:  {было}")
        print(f"    стало: {стало}   [{откуда}]")
    if предел > 0 and len(правки) > предел:
        print(f"\n  …и ещё {len(правки) - предел}. Весь список: --limit 0")

    if безнадёжных and getattr(args, "show_hopeless", False):
        print("\nНегодные без замены:")
        for doc_id, было, причина in безнадёжных[: предел or len(безнадёжных)]:
            print(f"  {doc_id}: «{было}» — {причина}")

    if not getattr(args, "apply", False):
        print("\nНичего не изменено. Применить: добавьте --apply")
        return 0

    if not правки:
        print("\nМенять нечего.")
        return 0

    изменено = 0
    with repos.db.transaction() as connection:
        for doc_id, _было, стало, _откуда in правки:
            connection.execute("UPDATE documents SET title = ? WHERE doc_id = ?",
                               (стало, doc_id))
            изменено += 1
    print(f"\nПереименовано документов: {изменено}")
    print("Названия изменены только в описи; сами файлы и фрагменты не тронуты.")
    return 0


def cmd_terms(args: argparse.Namespace) -> int:
    """Что прочитано из словаря терминов и что он сделает с вопросом.

    Словарь заявлен пополняемым, а строки отбрасывал молча: двухбуквенное
    сокращение, запись без эквивалентов, лишняя запятая в JSON — всё это
    выключало термин, а битый файл выключал весь словарь, и человек об этом
    не узнавал никак. Поиск при этом продолжал работать, просто хуже.
    """
    from .terms import MAX_EXPANSIONS, TermGlossary, default_path  # noqa: PLC0415

    settings = Settings.load()
    chosen = args.path or getattr(settings, "terms_path", None)
    path = Path(chosen) if chosen else default_path()
    glossary = TermGlossary.load(path)

    print(f"Словарь: {path}")
    print(f"Прочитано записей: {len(glossary)}")
    print("Из них с равнозначными русскими написаниями: "
          f"{sum(1 for term in glossary.terms if term.ru_syn)}")
    if glossary.problems:
        print("\nПропущено:")
        for trouble in glossary.problems:
            print(f"  {trouble}")

    if args.query:
        added = glossary.expand(args.query)
        print(f"\nК вопросу «{args.query}» добавится {len(added)} "
              f"из {MAX_EXPANSIONS} возможных:")
        for word in added:
            print(f"  {word}")
        if not added:
            print("  ничего — ни один термин словаря в вопросе не встретился")
    return 1 if (glossary.problems or not len(glossary)) else 0


def cmd_potok(args: argparse.Namespace) -> int:
    """Разобрать цифровой поток: код, скремблер, цикл, каналы, HDLC, IP.

    Тот же отчёт, что уходит модели, когда поток приложен к вопросу, — но
    без модели и без ограничения по объёму: для проверки на месте.
    """
    from .potok import разобрать  # noqa: PLC0415 — numpy нужен только здесь

    try:
        разбор = разобрать(args.path, глубоко=args.deep, снять=args.strip or (),
                           профиль="быстро" if args.fast else "обычно",
                           символ=[k for k in (args.bits or ()) if k % 2 == 0],
                           фм=[k for k in (args.bits or ()) if k % 2])
    except ValueError as ошибка:
        print(f"Указание не выполнено: {ошибка}")
        return 2
    print(разбор.отчёт(предел=args.limit))
    return 0 if разбор.находки else 1


def cmd_parts(args: argparse.Namespace) -> int:
    """Что прочитано из справочника состава и что он сделает с вопросом.

    Справочник пополняет отдел, а отбрасывать записи молча нельзя: слишком
    короткое название целого, запись без узлов, лишняя запятая в JSON — всё
    это выключает состав (а битый файл — весь справочник), и без этой
    команды человек не узнает об этом никак. Помощник при этом продолжает
    отвечать, просто хуже: на вопрос о тракте он снова не найдёт его узлы.
    """
    from .parts import MAX_UNITS, PartsBook, default_path  # noqa: PLC0415

    settings = Settings.load()
    выбранный = args.path or getattr(settings, "parts_path", None)
    path = Path(выбранный) if выбранный else default_path()
    книга = PartsBook.load(path)

    предел = int(args.limit or getattr(settings, "assistant_parts", MAX_UNITS))
    print(f"Справочник: {path}")
    print(f"Прочитано составов: {len(книга)}")
    if книга.problems:
        print("\nПропущено:")
        for беда in книга.problems:
            print(f"  {беда}")

    # Обрезание цепочки с конца — самое вредное, что тут может случиться:
    # дальний узел и есть ответ на «что принимает». Молча этого делать нельзя.
    длинные = [состав for состав in книга.compositions if len(состав.units) > предел]
    if длинные:
        print(f"\nБудут обрезаны (разбирается {предел} узлов, "
              f"настройка assistant_parts):")
        for состав in длинные:
            хвост = ", ".join(состав.units[предел:])
            print(f"  {состав.whole}: не поищется {хвост}")

    if args.query:
        узлы = книга.units_for(args.query, limit=предел)
        совпало = книга.match(args.query)
        if совпало:
            print(f"\nВ вопросе «{args.query}» названо: "
                  + ", ".join(состав.whole for состав in совпало))
        print(f"Помощник поищет отдельно {len(узлы)} "
              f"{'узел' if len(узлы) == 1 else 'узлов'}:")
        for узел in узлы:
            print(f"  {узел}")
        if not узлы:
            print("  ничего — ни один состав справочника в вопросе не встретился")
    elif not args.quiet:
        for состав in книга.compositions:
            print(f"\n{состав.whole}")
            if состав.also:
                print("  ещё пишут: " + ", ".join(состав.also))
            print("  узлы: " + ", ".join(состав.units))
    return 1 if (книга.problems or not len(книга)) else 0


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
        batch=settings.embed_batch,
    )
    # Пока строим — приложение к векторам не лезет: писать в SQLite можно
    # только по одному, и два построителя дают «database is locked» обоим.
    with console_work(repos.db.path, "построение векторов") as отметиться:
        def ход(готово: int, всего: int) -> None:
            отметиться()          # многочасовая работа не должна счесться брошенной
            print(готово, всего)

        # Батч уважаем и здесь: без него REPORTGEN_EMBED_BATCH не влиял на
        # построение индекса вовсе — всегда шли пачки по 16, сколько ни ставь.
        count = index_embeddings(repos, client, batch=settings.embed_batch,
                                 only_missing=not args.force, progress=ход)
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
    retriever = Retriever(BM25Index.load(args.index), terms_path=settings.terms_path) if args.index else None
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


def cmd_paths(args: argparse.Namespace) -> int:
    """Где лежат данные отдела: этим пользуются скрипты обслуживания.

    Скрипт, помнящий пути сам, рано или поздно разойдётся с приложением —
    и разойдётся молча. Пусть лучше спрашивает.
    """
    from .config import Settings  # noqa: PLC0415

    settings = Settings.load(args.config)
    места = settings.storage()
    if getattr(args, "json", False):
        print(json.dumps({"data_dir": str(settings.data_dir), "places": места},
                         ensure_ascii=False))
        return 0
    print("каталог данных: %s" % settings.data_dir)
    for место in места:
        отметка = "в копию" if место["в_копию"] else "       "
        существует = "есть" if Path(место["путь"]).exists() else "нет "
        print("  %s  %s  %-14s %s" % (отметка, существует, место["имя"], место["путь"]))
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
    p_useradd.add_argument(
        "--role", default="engineer", choices=list(ROLES),
        help="должность: " + "; ".join(f"{role} — {ROLE_TITLES[role]}" for role in ROLES),
    )
    p_useradd.add_argument(
        "--department", default="",
        help="подразделение по штату, если оно не то, где человек работает")
    p_useradd.add_argument("--team", default="", help="группа внутри отдела")
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
        "--reset", action="store_true",
        help="стереть библиотеку целиком и загрузить заново "
             "(письма, отчёты и военнослужащие не трогаются)",
    )
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

    p_retitle = sub.add_parser(
        "retitle",
        help="пересобрать негодные названия документов в описи (без перечитывания файлов)")
    p_retitle.add_argument("--doc-type", default=None)
    p_retitle.add_argument("--domain", default=None)
    p_retitle.add_argument("--limit", type=int, default=40,
                           help="сколько строк показать; 0 — все")
    p_retitle.add_argument("--show-hopeless", action="store_true",
                           help="показать и те, которым замены не нашлось")
    p_retitle.add_argument("--apply", action="store_true",
                           help="применить изменения (по умолчанию только показ)")
    p_retitle.set_defaults(func=cmd_retitle)

    p_formats = sub.add_parser("formats", help="какие форматы документов система умеет читать")
    p_formats.set_defaults(func=cmd_formats)

    p_terms = sub.add_parser("terms", help="проверить словарь терминов")
    p_terms.add_argument("--path", default=None, help="путь к terms.json")
    p_terms.add_argument("--query", default=None,
                         help="показать, что добавится к этому вопросу")
    p_terms.set_defaults(func=cmd_terms)

    p_potok = sub.add_parser(
        "potok", help="разобрать неизвестный цифровой поток на любом этапе: от кодирования "
                      "в линии до пакетов")
    p_potok.add_argument("path", help="файл потока: .bin, .sig, .dat, .raw, .bits, .hex, .pcap")
    p_potok.add_argument("--limit", type=int, default=100000,
                         help="сколько знаков отчёта печатать")
    p_potok.add_argument("--глубоко", "--deep", dest="deep", action="store_true",
                         help="глубокий разбор: длинные коды до 2048 бит, выколотые до 7/8, "
                              "до 20 минут")
    p_potok.add_argument("--быстро", "--fast", dest="fast", action="store_true",
                         help="быстрый разбор: короткие сроки слепых поисков, до полутора минут")
    p_potok.add_argument("--снять", "--strip", dest="strip", action="append", metavar="СЛОЙ",
                         help="сперва снять известный слой, дальше — вслепую; можно "
                              "несколько раз по порядку: «инверсия», «сдвиг 5», «nrzi», "
                              "«скремблер 3,20», «свёрточный 171/133 K=7», "
                              "«выколотый 171/133 K=7 шаблон 110110», «перемежение 12 7», "
                              "«pdh E2 приток 1», «плоскость 6»")
    p_potok.add_argument("--символ", "--bits", dest="bits", action="append", type=int,
                         metavar="K", help="бит на символ, если поток — метки демодулятора: "
                                           "чётное — КАМ (4, 6, 8…), нечётное — ФМ (1, 3 — 8PSK); "
                                           "без указания — по имени файла («…_8PSK_…»)")
    p_potok.set_defaults(func=cmd_potok)

    p_parts = sub.add_parser("parts", help="проверить справочник состава")
    p_parts.add_argument("--path", default=None, help="путь к parts.json")
    p_parts.add_argument("--query", default=None,
                         help="показать, какие узлы помощник поищет по этому вопросу")
    p_parts.add_argument("--limit", type=int, default=None,
                         help="сколько узлов брать (по умолчанию из настроек)")
    p_parts.add_argument("--quiet", action="store_true",
                         help="не печатать сами составы, только итог")
    p_parts.set_defaults(func=cmd_parts)

    p_audit = sub.add_parser(
        "audit", help="какие документы помощник не увидит и почему")
    p_audit.add_argument("--doc-type", default=None)
    p_audit.add_argument("--domain", default=None)
    p_audit.add_argument("--code", default=None,
                         help="показать только эту беду (нет-фрагментов, "
                              "текста-почти-нет, текст-склеен, нет-векторов, "
                              "название-негодное, обозначение-только-в-названии)")
    p_audit.add_argument("--limit", type=int, default=40,
                         help="сколько документов перечислить; 0 — все")
    p_audit.set_defaults(func=cmd_audit)

    p_why = sub.add_parser(
        "why", help="почему этот документ не выходит по этому вопросу")
    p_why.add_argument("--doc", required=True,
                       help="часть имени файла или названия («g.732»)")
    p_why.add_argument("--query", default=None,
                       help="вопрос, по которому документ должен был найтись")
    p_why.add_argument("--top-k", type=int, default=10)
    p_why.set_defaults(func=cmd_why)

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

    p_paths = sub.add_parser("paths", help="где лежат данные отдела")
    p_paths.add_argument("--json", action="store_true", help="вывод для скриптов")
    p_paths.set_defaults(func=cmd_paths)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FactPackError, FileNotFoundError, ValueError) as error:
        print(f"ошибка: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
