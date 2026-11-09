#!/usr/bin/env python3
"""Самопроверка скилла brd-digest-backend: ссылки, ядро, скрипт на реальных фикстурах.

Запуск: python scripts/selftest.py   (код 0 — всё в порядке, 1 — есть сбои)
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "brd_tools.py"
FIX = ROOT / "evals" / "fixtures"
failures = []


def check(name, ok, detail=""):
    print(("ок   " if ok else "СБОЙ ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run(*args):
    p = subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


# 1. Ссылки из SKILL.md ведут на существующие файлы
skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
for ref in sorted(set(re.findall(r"`((?:references|scripts)/[\w./-]+)`", skill))):
    check(f"файл существует: {ref}", (ROOT / ref).exists())

# 2. У ядра есть маркер версии
for f in sorted((ROOT / "references" / "core").glob("*.md")):
    check(f"маркер версии ядра: {f.name}", "Версия ядра:" in f.read_text(encoding="utf-8"))

# 3. Скрипт на реальных BRD (если фикстуры на месте — при поставке без evals пропускается)
v1, v0 = FIX / "brd-repo-v1.0.md", FIX / "brd-repo-v0.2.md"
if v1.exists() and v0.exists():
    code, out = run("ids", v1)
    check("ids: код 0", code == 0, out[:200])
    code, out = run("diff", v0, v1)
    check("diff: BR-35 найден как новый", code in (0, 1) and "BR-35" in out, out[:200])
    sample = ROOT / "references" / "worked-example-backend.md"
    code, out = run("coverage", v1, sample)
    check("coverage: образец покрывает все ID", code == 0, out[:300])
    tmp = ROOT / "evals" / "fixtures" / "_not_brd.tmp"
    tmp.write_text("Просто письмо без ID.\n", encoding="utf-8")
    try:
        code, out = run("ids", tmp)
        check("не BRD → код 2", code == 2, out[:200])
    finally:
        tmp.unlink()
else:
    print("пропуск: фикстур нет (поставка без evals)")

print()
print("ИТОГО: сбоев", len(failures))
sys.exit(1 if failures else 0)
