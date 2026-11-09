#!/usr/bin/env python3
"""Инструменты разбора BRD по шаблону ba-requirements-interviewer.

Три команды, все детерминированные — результат не зависит от суждения модели:

  ids      <brd.md>               инвентарь ID, определённых в таблицах BRD
  diff     <старый.md> <новый.md> что добавлено, удалено, изменено между версиями
  coverage <brd.md> <выжимка.md>  каждый ID из BRD должен встречаться в выжимке

Только стандартная библиотека. Коды выхода: 0 — всё сходится, 1 — найдены
расхождения, 2 — неверный вызов или файл не похож на BRD по шаблону.

Файл общий для скиллов brd-digest-*: правится в одном месте и копируется в
оба, расходящиеся копии ловит check-skill.py.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Префиксы ID из шаблона BRD (BR — требование, NFR — нефункциональное, AC — критерий
# приёмки, C — ограничение, R — риск, OQ — открытый вопрос). Набор взят из
# references/rules/id-integrity.md скилла ba-requirements-interviewer.
PREFIXES = ("BR", "NFR", "AC", "C", "R", "OQ")
ID_RE = re.compile(r"\b(?:%s)-\d{2,3}\b" % "|".join(PREFIXES))
# Определение ID — только в первой ячейке строки таблицы: упоминание в тексте
# другого требования не считается определением.
DEF_RE = re.compile(r"^(?:%s)-\d{2,3}$" % "|".join(PREFIXES))
TAG_RE = re.compile(r"\[[^\[\]]+\]")
# Диапазон вида «BR-18…BR-24» или «BR-18..BR-24»: шаблон BRD использует его в
# реестре ID. Одиночный дефис как разделитель не поддерживается — он путается с
# обычным текстом («BR-05 - 12 штук»).
RANGE_RE = re.compile(
    r"\b(%s)-(\d{2,3})\s*(?:…|\.\.|–|—)\s*(?:(?:%s)-)?(\d{2,3})\b"
    % ("|".join(PREFIXES), "|".join(PREFIXES))
)
PRIORITY_RE = re.compile(r"^(must|should|could|won'?t|won’t)\b", re.I)
VOLATILE_TAG_RE = re.compile(r"^\[изменено\b", re.I)


def fail(msg, code=2):
    print(f"[ОШИБКА] {msg}", file=sys.stderr)
    sys.exit(code)


def read(path):
    p = Path(path)
    if not p.is_file():
        fail(f"файл не найден: {path}")
    return p.read_text(encoding="utf-8", errors="replace")


def split_row(line):
    """Ячейки строки таблицы без крайних пустых."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def clean(text):
    """Убрать обратные кавычки и схлопнуть пробелы: сравнение не должно
    реагировать на оформление."""
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


# Заголовки колонок, в которых лежит формулировка. Порядок колонок в версиях шаблона
# менялся (в новых добавлены «Приоритет» и «Трассировка»), поэтому колонка ищется по
# названию, а не по номеру.
TEXT_HEADERS = ("требование", "ограничение", "риск", "критерий", "вопрос", "описание",
                "правило", "пункт")
SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")


def section_key(section):
    """Ключ раздела для сравнения версий: номер, если он есть. Переименование
    заголовка («Контроль, исправление» -> «Контроль и исправление») не должно
    выглядеть как перенос требования."""
    m = SECTION_NUM_RE.match(section + " ")
    return m.group(1) if m else clean(section).lower()


def is_separator(line):
    return bool(re.match(r"^\s*\|?\s*:?-{2,}", line)) and set(line.strip()) <= set("|-: ")


def parse_brd(text, source="BRD"):
    """Вернуть {id: запись}. Запись: kind, section, text, rest, tags, priority."""
    items = {}
    section = "(шапка)"
    header = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if h:
            section = clean(h.group(2))
            header = []
            continue
        if not line.lstrip().startswith("|"):
            header = []  # пустая строка или текст обрывает таблицу
            continue
        if is_separator(line):
            continue
        cells = split_row(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if is_separator(nxt):  # эта строка — заголовок таблицы
            header = [clean(c).lower() for c in cells]
            continue
        first = clean(cells[0]).strip("*")
        if not cells or not DEF_RE.match(first):
            continue
        rid = first
        body = [clean(c) for c in cells]
        hdr = header if len(header) == len(body) else []
        # формулировка
        idx = next((j for j, name in enumerate(hdr) if name in TEXT_HEADERS), 1)
        idx = idx if idx < len(body) else 1
        main = body[idx] if len(body) > idx else ""
        # приоритет
        pri = ""
        if hdr and "приоритет" in hdr:
            pri = body[hdr.index("приоритет")]
        else:
            pri = next((c for c in body[1:] if PRIORITY_RE.match(c)), "")
        pri = pri if PRIORITY_RE.match(pri) else ""
        joined = " | ".join(body[1:])
        tags = TAG_RE.findall(joined)
        rest_cells = [c for j, c in enumerate(body) if j not in (0, idx) and c != pri]
        rest = clean(TAG_RE.sub("", " | ".join(rest_cells)))
        rest = re.sub(r"\s*\|\s*(?=\|)", " ", rest)          # пустые ячейки
        rest = re.sub(r"(^|\|)\s*[;,.]\s*", r"\1 ", rest)     # ведущие «; » в ячейках
        rest = re.sub(r"\s+", " ", rest).strip(" ;|")
        if rid in items:
            items[rid].setdefault("dups", []).append(section)
            continue
        items[rid] = {
            "id": rid,
            "kind": rid.split("-")[0],
            "section": section,
            "section_key": section_key(section),
            "text": clean(TAG_RE.sub("", main)),
            "rest": rest,
            "tags": sorted({t for t in tags if not VOLATILE_TAG_RE.match(t)}),
            "changed_marker": any(VOLATILE_TAG_RE.match(t) for t in tags),
            "priority": pri.lower(),
        }
    if not items:
        fail(
            f"в {source} не найдено ни одного ID вида BR-01 в первой колонке таблиц. "
            "Это не BRD по шаблону ba-requirements-interviewer: разбор опирается на "
            "ID, секции и теги шаблона. Попроси BRD по шаблону или собери его через "
            "ba-requirements-interviewer."
        )
    return items


def expand_ids(text):
    """Все ID, упомянутые в тексте, включая раскрытые диапазоны."""
    found = set(ID_RE.findall(text))
    for m in RANGE_RE.finditer(text):
        prefix, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        width = len(m.group(2))
        if b < a or b - a > 200:
            continue  # заведомо не диапазон ID
        for n in range(a, b + 1):
            found.add(f"{prefix}-{n:0{width}d}")
    return found


def cmd_ids(args):
    items = parse_brd(read(args.brd), args.brd)
    rows = [v for v in items.values() if not args.kind or v["kind"] == args.kind]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"| ID | Секция | Приоритет | Теги | Текст |")
    print("|---|---|---|---|---|")
    for v in rows:
        text = v["text"][:110] + ("…" if len(v["text"]) > 110 else "")
        print(f"| {v['id']} | {v['section']} | {v['priority'] or '—'} | "
              f"{' '.join(v['tags']) or '—'} | {text} |")
    by_kind = {}
    for v in items.values():
        by_kind[v["kind"]] = by_kind.get(v["kind"], 0) + 1
    print("\nИтого: " + ", ".join(f"{k} — {n}" for k, n in sorted(by_kind.items())))
    dups = [(k, v["dups"]) for k, v in items.items() if v.get("dups")]
    for k, secs in dups:
        print(f"[ВНИМАНИЕ] {k} определён повторно в секциях: {', '.join(secs)}")
    return 0


def context_line(text, rid):
    """Строка BRD, где ID упомянут вне таблицы-определения (например, в абзаце
    «Закрыты в ходе интервью»)."""
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            continue
        if rid in expand_ids(line):
            return clean(line)[:240]
    return ""


def cmd_diff(args):
    old_text, new_text = read(args.old), read(args.new)
    old = parse_brd(old_text, args.old)
    new = parse_brd(new_text, args.new)
    added = [new[k] for k in new if k not in old]
    gone = [old[k] for k in old if k not in new]
    removed, moved_to_prose = [], []
    new_mentions = expand_ids(new_text)
    for v in gone:
        (moved_to_prose if v["id"] in new_mentions else removed).append(v)
    changed, prio_assigned = [], []
    for k in new:
        if k not in old:
            continue
        o, n = old[k], new[k]
        what = []
        if o["text"] != n["text"]:
            what.append("текст")
        if o["rest"] != n["rest"]:
            what.append("детали")
        if o["priority"] != n["priority"]:
            if not o["priority"] and n["priority"]:
                prio_assigned.append(k)  # массовая простановка — свернём в одну строку
            else:
                what.append("приоритет")
        if o["tags"] != n["tags"]:
            what.append("теги")
        if o["section_key"] != n["section_key"]:
            what.append("секция")
        if n["changed_marker"] and not o["changed_marker"]:
            what.append("помечено [изменено]")
        if what:
            changed.append({"id": k, "what": what, "old": o, "new": n})
    if args.json:
        print(json.dumps({"added": added, "removed": removed,
                          "moved_to_prose": moved_to_prose, "changed": changed,
                          "priority_assigned": prio_assigned},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"## Изменено существующее: {len(changed)}")
    if changed:
        print("| ID | Что изменилось | Было | Стало |")
        print("|---|---|---|---|")
        for c in changed:
            o, n = c["old"], c["new"]
            was, now = [], []
            for field, label in (("text", "текст"), ("rest", "детали")):
                if label in c["what"]:
                    was.append(o[field] or "—"); now.append(n[field] or "—")
            if "приоритет" in c["what"]:
                was.append(f"приоритет: {o['priority'] or '—'}")
                now.append(f"приоритет: {n['priority'] or '—'}")
            if "теги" in c["what"]:
                was.append("теги: " + (" ".join(o["tags"]) or "—"))
                now.append("теги: " + (" ".join(n["tags"]) or "—"))
            if "секция" in c["what"]:
                was.append(f"секция: {o['section']}"); now.append(f"секция: {n['section']}")
            if not was:
                was, now = ["—"], ["(в документе добавлена пометка об изменении)"]
            print(f"| {c['id']} | {', '.join(c['what'])} | {' ⏐ '.join(was)} | "
                  f"{' ⏐ '.join(now)} |")
    if prio_assigned:
        print(f"\nПриоритеты проставлены впервые ({len(prio_assigned)}): "
              + ", ".join(prio_assigned))
    print(f"\n## Новое: {len(added)}")
    for v in added:
        print(f"- {v['id']} ({v['section']}): {v['text'][:140]}")
    print(f"\n## Ушло из таблиц, но упомянуто в тексте: {len(moved_to_prose)}")
    for v in moved_to_prose:
        print(f"- {v['id']}: {context_line(new_text, v['id']) or '(упоминание не в отдельной строке)'}")
    print(f"\n## Исчезло из документа совсем: {len(removed)}")
    for v in removed:
        print(f"- {v['id']} ({v['section']}): {v['text'][:140]}")
    if removed:
        print("[ВНИМАНИЕ] По правилу целостности ID требование не исчезает, а меняет "
              "статус. Проверь, не потеряно ли оно.")
    return 0


def cmd_coverage(args):
    brd_text = read(args.brd)
    items = parse_brd(brd_text, args.brd)
    digest = read(args.digest)
    mentioned = expand_ids(digest)
    defined = set(items)
    known = expand_ids(brd_text)  # ID, упомянутые в BRD где угодно, в том числе в абзацах
    missing = sorted(defined - mentioned)
    foreign = sorted(mentioned - known)
    if args.json:
        print(json.dumps({"defined": len(defined), "covered": len(defined & mentioned),
                          "missing": missing, "foreign": foreign},
                         ensure_ascii=False, indent=2))
    else:
        print(f"ID в BRD: {len(defined)}; встречаются в выжимке: "
              f"{len(defined & mentioned)}")
        if missing:
            print("\n[ОШИБКА] Не покрыты (нет ни в разделах, ни в списке «Опущено»):")
            for k in missing:
                print(f"  - {k} ({items[k]['section']}): {items[k]['text'][:90]}")
        if foreign:
            print("\n[ОШИБКА] ID есть в выжимке, но не определены в BRD "
                  "(выдуманы или опечатка):")
            for k in foreign:
                print(f"  - {k}")
        if not missing and not foreign:
            print("Покрытие полное, посторонних ID нет.")
    return 1 if (missing or foreign) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ids", help="инвентарь ID из BRD")
    a.add_argument("brd")
    a.add_argument("--kind", choices=PREFIXES, help="только один префикс")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_ids)
    b = sub.add_parser("diff", help="сравнить две версии BRD")
    b.add_argument("old")
    b.add_argument("new")
    b.add_argument("--json", action="store_true")
    b.set_defaults(fn=cmd_diff)
    c = sub.add_parser("coverage", help="проверить покрытие ID в выжимке")
    c.add_argument("brd")
    c.add_argument("digest")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_coverage)
    args = ap.parse_args()
    try:
        sys.exit(args.fn(args))
    except BrokenPipeError:
        sys.exit(0)  # вывод обрезан внешним head/less — не ошибка


if __name__ == "__main__":
    main()
