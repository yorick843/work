#!/usr/bin/env python3
"""
check-skill.py — проверка скилла на соответствие стандарту.

Проверяет только то, что проверяется механически: формат frontmatter,
бюджеты контекста, связность ссылок, гигиену пакета. Содержательные
требования стандарта (качество description, степени свободы, наличие
"почему") проверяются человеком по чек-листу.

Использование:
    python3 check-skill.py <путь-к-скиллу> [<ещё-скилл> ...]

Код возврата: 0 — ошибок нет, 1 — есть ошибки.
"""

import json
import re
import sys
import pathlib
import difflib

SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
SERVICE_FILES = {"README.md", "CHANGELOG.md", "LICENSE", "LICENSE.md", ".gitignore"}
JUNK = {"__pycache__", ".DS_Store", ".ipynb_checkpoints", ".writetest"}

BODY_LINES_LIMIT = 500
BODY_TOKENS_LIMIT = 5000          # одноразовый скилл
BODY_TOKENS_LIMIT_SESSION = 8000  # сессионный: тело амортизируется по длинной сессии
TOC_LINES_THRESHOLD = 100
# Поздний сценарий должен содержать сессию, а не описание: транскрипт в files или промпт
# такой длины, какой не бывает у описания. 1500 символов — примерно пять-шесть реплик;
# описание «интервью идёт шестьдесят реплик» укладывается в 200–400.
LATE_PROMPT_MIN = 1500
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def est_tokens(text: str) -> int:
    """Грубая оценка: кириллица дороже латиницы примерно вдвое."""
    cyr = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    return int(cyr / 2.2 + (len(text) - cyr) / 4)


def skill_version(path: pathlib.Path):
    """Версия скилла: metadata.version, иначе верхняя запись CHANGELOG. (источник, строка, кортеж)."""
    sk = path / "SKILL.md"
    if sk.exists():
        fm, _ = parse_front(sk.read_text(encoding="utf-8", errors="replace"))
        if fm and "metadata" in fm:
            m = re.search(r"version:\s*([0-9][0-9.]*)", fm["metadata"])
            if m:
                return "metadata", m.group(1), tuple(int(x) for x in m.group(1).split("."))
    ch = path / "CHANGELOG.md"
    if ch.exists():
        m = re.search(r"^##\s*v?([0-9][0-9.]*)", ch.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return "CHANGELOG", m.group(1), tuple(int(x) for x in m.group(1).split("."))
    return None, None, None


def parse_front(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None, text
    raw = m.group(1)
    fm, key = {}, None
    for line in raw.split("\n"):
        km = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if km:
            key = km.group(1)
            fm[key] = km.group(2).strip().strip('"\'') if km.group(1) in ("name",) else km.group(2).strip()
        elif key and line.strip():
            fm[key] += " " + line.strip()
    return fm, text[m.end():]


def links_in(text):
    """Ссылки на файлы пакета: markdown-ссылки и голые пути."""
    out = set()
    for m in re.finditer(r"\]\(([^)\s#]+)\)", text):
        out.add(m.group(1))
    for m in re.finditer(r"(?<![\w/.])((?:references|scripts|assets|evals)/[\w./-]+\.\w+)", text):
        out.add(m.group(1))
    return {p for p in out if not p.startswith(("http://", "https://", "mailto:"))}


def check(path: pathlib.Path):
    err, warn, info = [], [], []
    E, W, I = err.append, warn.append, info.append

    sk = path / "SKILL.md"
    if not sk.exists():
        E(f"нет SKILL.md в корне {path}")
        return err, warn, info, {}

    raw = sk.read_text(encoding="utf-8", errors="replace")
    if not raw.startswith("---"):
        E("frontmatter не начинается с первого байта файла")
    fm, body = parse_front(raw)
    if fm is None:
        E("frontmatter не разобран (нет закрывающего ---)")
        fm, body = {}, raw

    # --- frontmatter
    name = fm.get("name", "")
    if not name:
        E("нет обязательного поля name")
    else:
        if len(name) > 64:
            E(f"name длиннее 64 символов ({len(name)})")
        if not NAME_RE.match(name):
            E(f"name '{name}' нарушает формат: только a-z, 0-9 и дефис, без крайних дефисов")
        if name != path.name:
            E(f"name '{name}' не совпадает с именем папки '{path.name}' — скилл не загрузится")
        if any(w in name.lower() for w in ("anthropic", "claude")):
            E(f"name '{name}' содержит зарезервированное слово")

    desc = fm.get("description", "")
    if not desc:
        E("нет обязательного поля description")
    else:
        if len(desc) > 1024:
            E(f"description длиннее 1024 символов ({len(desc)})")
        elif len(desc) > 900:
            W(f"description {len(desc)} симв. — близко к лимиту 1024, запас на правку мал")
        if "<" in desc or ">" in desc:
            E("в description есть угловые скобки — риск инъекции в системный промпт")
        if re.search(r"\b(я могу|я помогу|вы можете|I can|I will|you can)\b", desc, re.I):
            W("description написан от первого/второго лица — нужен третий")

    for k in fm:
        if k not in SPEC_KEYS:
            W(f"поле frontmatter '{k}' вне спецификации — часть рантаймов отвергнет скилл")
    if "compatibility" in fm and len(fm["compatibility"]) > 500:
        E("compatibility длиннее 500 символов")
    if "metadata" not in fm:
        W("нет metadata — версия скилла не читается машинно")
    elif "version" not in fm["metadata"]:
        W("в metadata нет version — непонятно, какая версия скилла установлена")

    # --- класс скилла и бюджет тела
    cls = "oneshot"
    if "metadata" in fm and re.search(r"type:\s*session", fm["metadata"]):
        cls = "session"

    late = False
    ev_path = path / "evals" / "evals.json"
    # Установленная копия: упаковщик (package_skill) исключает evals/ из .skill, поэтому у
    # установленного скилла evals нет, даже если в исходниках они есть. Признак — README или
    # CHANGELOG их упоминают. Без этой проверки аудит установленной копии сообщает ложные ошибки.
    installed_copy = not (path / "evals").exists() and any(
        (path / f).exists() and "evals" in (path / f).read_text(encoding="utf-8", errors="replace")
        for f in ("README.md", "CHANGELOG.md"))
    if ev_path.exists():
        try:
            evs = json.loads(ev_path.read_text(encoding="utf-8")).get("evals", [])
            late_evs = [e for e in evs if e.get("phase") == "late"]
            late = bool(late_evs)
            for e in late_evs:
                files = e.get("files") or []
                missing = [f for f in files if not (path / "evals" / f).exists()]
                if missing:
                    W(f"поздний сценарий {e.get('id')}: файлы из files не найдены в evals/: {', '.join(missing)}")
                elif not files and len(e.get("prompt", "")) < LATE_PROMPT_MIN:
                    W(f"поздний сценарий {e.get('id')} описывает сессию, а не содержит её (нет files, "
                      f"промпт {len(e.get('prompt', ''))} симв.) — разбавление внимания не воспроизводится, "
                      "класс session подтверждён формально; нужен транскрипт в files")
        except Exception:  # noqa: BLE001
            E("evals/evals.json не разбирается")

    limit = BODY_TOKENS_LIMIT
    if cls == "session":
        if late:
            limit = BODY_TOKENS_LIMIT_SESSION
        elif installed_copy:
            limit = BODY_TOKENS_LIMIT_SESSION  # класс не проверить; итог — в предупреждении ниже
        else:
            E("класс session объявлен, но нет сценария с phase: late — освобождение от порога "
              f"{BODY_TOKENS_LIMIT} не подтверждено: длинное тело проверяется только прогоном "
              "на конце длинной сессии")

    lines, toks = body.count("\n"), est_tokens(body)
    if lines > BODY_LINES_LIMIT:
        W(f"тело SKILL.md {lines} строк (порог {BODY_LINES_LIMIT})")
    if toks > limit:
        E(f"тело SKILL.md ~{toks} токенов (порог {limit}, класс {cls}) — грузится целиком при каждом срабатывании")
    elif cls == "session" and toks > BODY_TOKENS_LIMIT:
        I(f"тело ~{toks} токенов — в пределах порога сессионного класса, но ветки из тела всё равно выносятся")
    if "\\" in re.sub(r"`[^`]*`", "", body) and re.search(r"\w\\\w+\.(md|py|css|json)", body):
        E("пути с обратными слэшами — сломаются вне Windows")

    # capslock-императивы: маркер требования без объяснённой причины
    caps = re.findall(r"\b(ВСЕГДА|НИКОГДА|ОБЯЗАТЕЛЬНО|ЗАПРЕЩЕНО|ALWAYS|NEVER|MUST)\b", body)
    if len(caps) > 3:
        W(f"капслочных императивов: {len(caps)} (порог 3) — вероятно, причина требования не сформулирована")

    # --- связность
    all_files = {
        p.relative_to(path).as_posix()
        for p in path.rglob("*")
        if p.is_file() and not any(j in p.parts for j in JUNK)
    }
    lvl1 = links_in(body)
    for ref in sorted(lvl1):
        if ref.endswith("/"):
            if not (path / ref).is_dir():
                E(f"SKILL.md ссылается на несуществующую папку {ref}")
        elif ref not in all_files:
            E(f"SKILL.md ссылается на несуществующий файл {ref}")

    lvl2 = set()
    for ref in lvl1:
        f = path / ref
        if f.is_file() and f.suffix == ".md":
            for r in links_in(f.read_text(encoding="utf-8", errors="replace")):
                # evals/ моделью в работе не читается — упоминание в справочнике не дефект
                if r.startswith("evals/"):
                    continue
                if r in all_files and r not in lvl1:
                    lvl2.add((ref, r))
    for src, dst in sorted(lvl2):
        W(f"вложенная ссылка: {dst} доступен только через {src} — читается частично")

    reachable = lvl1 | {d for _, d in lvl2}
    for f in sorted(all_files):
        top = f.split("/")[0]
        if f == "SKILL.md" or f in SERVICE_FILES or top == "evals":
            continue
        if f not in reachable:
            W(f"файл {f} не упомянут в SKILL.md — модель его не найдёт")

    # --- оглавления
    for f in sorted(all_files):
        if not f.startswith("references/") or not f.endswith(".md"):
            continue
        t = (path / f).read_text(encoding="utf-8", errors="replace")
        if t.count("\n") > TOC_LINES_THRESHOLD:
            head = "\n".join(t.split("\n")[:25])
            if not re.search(r"(Содержание|Оглавление|Contents|^\s*[-*]\s*\[)", head, re.M):
                W(f"{f}: {t.count(chr(10))} строк без оглавления в начале")

    # --- карта состава в README: набор файлов и описание в README расходятся молча,
    # поэтому сверяем их механически (предупреждение, не ошибка: README — для человека)
    readme = path / "README.md"
    if readme.exists():
        rt = readme.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^##\s+(Состав|Структура)[^\n]*\n(.*?)(?=^##\s|\Z)", rt, re.M | re.S)
        if not m:
            W("в README нет раздела «Состав» с картой файлов пакета")
        else:
            listed = set(re.findall(r"`([\w./-]+)`", m.group(2)))
            dirs = {x for x in listed if x.endswith("/")}
            for f in sorted(all_files):
                if f in SERVICE_FILES or f == "SKILL.md":
                    continue
                if f in listed or any(f.startswith(d) for d in dirs):
                    continue
                W(f"файл {f} не описан в разделе «Состав» README")
            for x in sorted(listed):
                if "/" not in x:
                    continue  # голое имя или расширение в тексте, не путь пакета
                if x.startswith("evals/") and not (path / "evals").exists():
                    continue  # установленная копия без evals
                if not (path / x).exists():
                    W(f"README «Состав» называет несуществующий путь {x}")

    # --- проверяемость и гигиена
    if installed_copy:
        W("нет evals/, но README или CHANGELOG их упоминают — похоже на установленную копию: "
          "упаковщик исключает evals/ из .skill. Evals и класс не проверить, порог тела взят по "
          "объявленному классу. Аудит — по исходникам")
    elif not (path / "evals").exists():
        W("нет evals/ — соответствие стандарту не проверяется прогонами")
    for p in path.rglob("*"):
        if any(j in p.parts for j in JUNK) or p.name in JUNK:
            W(f"мусор в пакете: {p.relative_to(path)}")
            break

    stats = {
        "name": name,
        "class": cls,
        "desc": len(desc),
        "lines": lines,
        "tokens": toks,
        "files": len(all_files),
        "bytes": sum((path / f).stat().st_size for f in all_files),
        "description": desc,
    }
    return err, warn, info, stats


def cross_check(paths, stats):
    """Сравнение скиллов между собой: дрейф копий и конкуренция описаний."""
    out = []
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            a, b = paths[i], paths[j]
            sa, sb = stats.get(a.name, {}), stats.get(b.name, {})
            da, db = sa.get("description", ""), sb.get("description", "")
            if da and db:
                sim = difflib.SequenceMatcher(None, da, db).ratio()
                if sim > 0.6:
                    out.append(f"описания {a.name} и {b.name} схожи на {sim:.0%} — скиллы будут конкурировать за срабатывание")
            fa = {p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file()}
            fb = {p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file()}
            common = sorted((fa & fb) - {"SKILL.md", "README.md", "CHANGELOG.md", ".gitignore"})
            drift = [f for f in common
                     if (a / f).read_bytes() != (b / f).read_bytes()]
            if drift:
                out.append(f"копии разошлись: {a.name} / {b.name} — {', '.join(drift)}")
                (sa_src, sa_v, sa_t), (sb_src, sb_v, sb_t) = skill_version(a), skill_version(b)
                if sa_t and sb_t and sa_t != sb_t:
                    if sa_t > sb_t:
                        old_p, old_v, new_p, new_v = b, sb_v, a, sa_v
                    else:
                        old_p, old_v, new_p, new_v = a, sa_v, b, sb_v
                    out.append(
                        f"направление расхождения: {old_p.name} отстаёт (v{old_v} против v{new_v}) — "
                        f"базой берётся {new_p.name}; источник версии: {sa_src or sb_src}")
                elif not (sa_t and sb_t):
                    out.append("направление расхождения не определить: нет metadata.version и разбираемого CHANGELOG — "
                               "какая копия свежее, придётся выяснять вручную")
    return out


def main(argv):
    paths = [pathlib.Path(p).resolve() for p in argv] or [pathlib.Path(".").resolve()]
    total_err = 0
    stats_all = {}
    for p in paths:
        err, warn, info, st = check(p)
        stats_all[p.name] = st
        total_err += len(err)
        print("=" * 72)
        print(f"{p.name}")
        if st:
            print(f"  класс: {st['class']} | тело: {st['lines']} строк / ~{st['tokens']} токенов | "
                  f"description: {st['desc']} симв. | файлов: {st['files']} | {st['bytes'] // 1024} КБ")
        for m in err:
            print(f"  [ОШИБКА]  {m}")
        for m in warn:
            print(f"  [ВНИМАНИЕ] {m}")
        for m in info:
            print(f"  [заметка] {m}")
        if not err and not warn:
            print("  соответствует механической части стандарта")
    if len(paths) > 1:
        cross = cross_check(paths, stats_all)
        if cross:
            print("=" * 72)
            print("МЕЖДУ СКИЛЛАМИ")
            for m in cross:
                print(f"  [ВНИМАНИЕ] {m}")
    print("=" * 72)
    print(f"ИТОГО: ошибок {total_err}")
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
