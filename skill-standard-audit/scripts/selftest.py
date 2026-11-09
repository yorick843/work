#!/usr/bin/env python3
"""
selftest.py — самопроверка скилла skill-standard-audit.

Проверяет внутреннюю согласованность: жив ли стандарт, на который скилл ссылается,
сходятся ли счётчики, разбирается ли набор сценариев, проходит ли скилл собственный
валидатор. Запускать после любой правки скилла.

Использование: python3 scripts/selftest.py   (из корня скилла)
"""

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'СБОЙ'}  {name}{'' if cond else ' — ' + detail}")
    if not cond:
        fails.append(name)


print("=== состав пакета ===")
required = [
    "SKILL.md",
    "README.md",
    "CHANGELOG.md",
    "references/standard.md",
    "references/migration-playbook.md",
    "references/eval-templates.md",
    "scripts/check-skill.py",
    "scripts/selftest.py",
    "evals/evals.json",
    "evals/trigger-evals.json",
]
for f in required:
    check(f"{f} на месте", (ROOT / f).exists())

skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
standard = (ROOT / "references/standard.md").read_text(encoding="utf-8")

print("=== ссылки из SKILL.md ===")
refs = set(re.findall(r"`((?:references|scripts)/[\w./-]+)`", skill))
for r in sorted(refs):
    check(f"ссылка {r} разрешается", (ROOT / r).exists())
check(
    "все ресурсы пакета упомянуты в SKILL.md",
    refs >= {f for f in required if f.startswith(("references/", "scripts/"))},
    f"не упомянуты: {sorted({f for f in required if f.startswith(('references/', 'scripts/'))} - refs)}",
)

print("=== согласованность со стандартом ===")
check(
    "у стандарта есть оглавление",
    bool(re.search(r"^## Содержание", standard, re.M)),
)
sections = re.findall(r"^## (\d+)\.", standard, re.M)
check(
    "разделы стандарта пронумерованы подряд",
    [int(s) for s in sections] == list(range(1, len(sections) + 1)),
    f"найдено: {sections}",
)
items = [int(m) for m in re.findall(r"^(\d+)\. ", standard.split("## 15.")[-1].split("## 16.")[0], re.M)]
check(
    "чек-лист стандарта пронумерован подряд",
    items == list(range(1, len(items) + 1)) and len(items) > 20,
    f"пунктов: {len(items)}",
)
for ref in ("раздел 5", "раздел 15", "раздела 15", "раздела 9"):
    pass
check(
    "SKILL.md ссылается на разделы стандарта, которые существуют",
    all(
        int(n) <= len(sections)
        for n in re.findall(r"раздел[а]?\s+(\d+)\s+стандарта", skill)
    ),
    "есть ссылка на несуществующий раздел",
)

print("=== сценарии ===")
try:
    ev = json.loads((ROOT / "evals/evals.json").read_text(encoding="utf-8"))
    check("evals.json разбирается", True)
    check("сценариев не меньше трёх", len(ev.get("evals", [])) >= 3, str(len(ev.get("evals", []))))
    check(
        "у каждого сценария есть проверяемые ожидания",
        all(len(e.get("expected_behavior", [])) >= 2 for e in ev.get("evals", [])),
    )
    check("skill_name совпадает с именем папки", ev.get("skill_name") == ROOT.name)
except Exception as e:  # noqa: BLE001
    check("evals.json разбирается", False, str(e))

try:
    tr = json.loads((ROOT / "evals/trigger-evals.json").read_text(encoding="utf-8"))
    pos = sum(1 for q in tr if q["should_trigger"])
    check("триггерных запросов не меньше 20", len(tr) >= 20, str(len(tr)))
    check("отрицательных не меньше 10", len(tr) - pos >= 10, f"{len(tr) - pos}")
    check("положительных не меньше 8", pos >= 8, str(pos))
except Exception as e:  # noqa: BLE001
    check("trigger-evals.json разбирается", False, str(e))

print("=== валидатор на фикстурах ===")
import tempfile


def mkskill(root, name, session=True, readme="", evals=None, files=None):
    d = pathlib.Path(root) / name
    (d / "references").mkdir(parents=True)
    meta = "metadata:\n  version: 0.1\n  updated: 2026-01-01\n" + ("  type: session\n" if session else "")
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Тестовая фикстура. Использовать для проверки валидатора.\n{meta}---\n\n# T\n\nТело.\n", encoding="utf-8")
    if readme:
        (d / "README.md").write_text(readme, encoding="utf-8")
    if evals is not None:
        (d / "evals").mkdir()
        (d / "evals/evals.json").write_text(json.dumps({"skill_name": name, "evals": evals}, ensure_ascii=False), encoding="utf-8")
    for f, body in (files or {}).items():
        (d / "evals" / f).parent.mkdir(parents=True, exist_ok=True)
        (d / "evals" / f).write_text(body, encoding="utf-8")
    return d


def run(d):
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check-skill.py"), str(d)], capture_output=True, text=True)
    return r.stdout


with tempfile.TemporaryDirectory() as tmp:
    out = run(mkskill(tmp, "installed-copy", readme="Сценарии лежат в evals/."))
    check("установленная копия: предупреждение вместо ошибки класса",
          "похоже на установленную копию" in out and "нет сценария с phase: late" not in out, out)
    out = run(mkskill(tmp, "no-evals"))
    check("без evals и без упоминаний: ошибка класса как раньше", "нет сценария с phase: late" in out, out)
    late_desc = [{"id": 1, "phase": "late", "prompt": "Интервью идёт шестьдесят реплик.", "expected_behavior": ["a", "b"], "files": []}]
    out = run(mkskill(tmp, "late-description", evals=late_desc))
    check("поздний сценарий-описание: предупреждение", "описывает сессию, а не содержит её" in out, out)
    late_tr = [{"id": 1, "phase": "late", "prompt": "Последняя реплика.", "expected_behavior": ["a", "b"], "files": ["fixtures/t.md"]}]
    out = run(mkskill(tmp, "late-transcript", evals=late_tr, files={"fixtures/t.md": "R1 ... R60"}))
    check("поздний сценарий с транскриптом: без предупреждения", "описывает сессию" not in out and "не найдены" not in out, out)
    out = run(mkskill(tmp, "late-missing", evals=late_tr))
    check("поздний сценарий со ссылкой на отсутствующий файл: предупреждение", "не найдены в evals/" in out, out)

    out = run(mkskill(tmp, "no-map", readme="Только описание."))
    check("README без «Состав»: предупреждение", "нет раздела «Состав»" in out, out)
    out = run(mkskill(tmp, "map-ok", readme="## Состав\n\n| Путь | Назначение |\n|---|---|\n| `references/` | справочники |\n"))
    check("README с картой: без предупреждений о карте", "Состав" not in out, out)
    out = run(mkskill(tmp, "map-stale", readme="## Состав\n\n| `references/` | а |\n| `scripts/gone.py` | нет такого |\n"))
    check("карта с несуществующим путём: предупреждение", "несуществующий путь scripts/gone.py" in out, out)

print("=== собственный валидатор ===")
res = subprocess.run(
    [sys.executable, str(ROOT / "scripts/check-skill.py"), str(ROOT)],
    capture_output=True,
    text=True,
)
check("скилл проходит собственный check-skill.py без ошибок", res.returncode == 0,
      "\n" + res.stdout)

print()
if fails:
    print(f"СБОЕВ: {len(fails)} — {', '.join(fails)}")
    sys.exit(1)
print("все проверки пройдены")
