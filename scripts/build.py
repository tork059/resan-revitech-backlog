#!/usr/bin/env python3
"""Сборка бэклога.

Команды:
  python scripts/build.py                 — пересобрать README, архив и данные дашборда
  python scripts/build.py new --title "…" [--priority must] [--type dev] [--system 1С:УТ]
                                          [--initiator …] [--deadline 2026-10-15]
                                          [--estimate 2d] [--bitrix URL] [--status backlog]
  python scripts/build.py from-issue EVENT.json — создать задачу из формы GitHub Issue

Без внешних зависимостей: только стандартная библиотека Python 3.9+.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"
ARCHIVE = ROOT / "archive"
TEMPLATE = ROOT / "templates" / "task.md"
SITE = ROOT / "_site"
CONFIG = json.loads((ROOT / "backlog.json").read_text(encoding="utf-8"))

STATUSES = {
    "inbox": ("Входящие", "📥"),
    "backlog": ("Бэклог", "🗂"),
    "in-progress": ("В работе", "🎯"),
    "waiting": ("Ожидание", "⏳"),
    "done": ("Готово", "✅"),
    "canceled": ("Отменено", "🚫"),
}
CLOSED = {"done", "canceled"}
PRIORITIES = {
    "must": ("Must", "обязательно", "🔴"),
    "should": ("Should", "важно", "🟠"),
    "could": ("Could", "желательно", "🔵"),
    "wont": ("Won't", "не сейчас", "⚪"),
}
PRIO_ORDER = list(PRIORITIES)
TYPES = {
    "analysis": "Аналитика",
    "dev": "Разработка",
    "consult": "Консультация",
    "research": "Исследование",
    "support": "Поддержка",
}
PROJECTS = CONFIG.get("projects", {})
FIELDS = ["id", "title", "status", "priority", "type", "project", "system", "initiator",
          "created", "deadline", "estimate", "bitrix", "tags", "closed"]

# Русские синонимы — чтобы при ручной правке можно было писать по-русски.
ALIASES = {
    "status": {"входящие": "inbox", "бэклог": "backlog", "в работе": "in-progress",
               "ожидание": "waiting", "готово": "done", "отменено": "canceled",
               "todo": "backlog", "wip": "in-progress"},
    "priority": {"won't": "wont", "обязательно": "must", "важно": "should",
                 "желательно": "could", "не сейчас": "wont"},
    "type": {"аналитика": "analysis", "разработка": "dev", "консультация": "consult",
             "исследование": "research", "поддержка": "support", "development": "dev"},
}

WARNINGS: list[str] = []


# ───────────────────────── даты ─────────────────────────
def today() -> dt.date:
    tz = dt.timezone(dt.timedelta(hours=CONFIG.get("timezone_offset_hours", 5)))
    forced = os.environ.get("BACKLOG_TODAY")
    return dt.date.fromisoformat(forced) if forced else dt.datetime.now(tz).date()


def parse_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


# ─────────────────────── frontmatter ───────────────────────
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def strip_comment(v: str) -> str:
    return re.split(r"(?:^|\s)#\s", v, maxsplit=1)[0].strip()


def parse_task(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        WARNINGS.append(f"`{rel(path)}` — нет блока свойств `---`, файл пропущен")
        return None
    meta: dict = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        meta[key.strip().lower()] = strip_comment(val).strip().strip('"').strip("'")
    tags = meta.get("tags", "").strip("[]")
    meta["tags"] = [t.strip().strip('"\'') for t in tags.split(",") if t.strip()]
    for key in ("status", "priority", "type"):
        raw = meta.get(key, "").strip().lower()
        meta[key] = ALIASES.get(key, {}).get(raw, raw)
    meta["body"] = text[m.end():].strip()
    meta["path"] = path
    validate(meta)
    return meta


def validate(t: dict) -> None:
    where = f"`{rel(t['path'])}`"
    if not t.get("id"):
        WARNINGS.append(f"{where} — не указан `id`")
    if not t.get("title"):
        WARNINGS.append(f"{where} — не указано название `title`")
    if t.get("status") not in STATUSES:
        WARNINGS.append(f"{where} — неизвестный статус `{t.get('status')}`, считаю «Входящие»")
        t["status"] = "inbox"
    if t.get("priority") not in PRIORITIES:
        if t.get("priority"):
            WARNINGS.append(f"{where} — неизвестный приоритет `{t.get('priority')}`, считаю Should")
        t["priority"] = "should"
    if t.get("project") and t["project"] not in PROJECTS:
        WARNINGS.append(f"{where} — неизвестный проект `{t['project']}` (список — в backlog.json)")
    if t.get("type") and t["type"] not in TYPES:
        WARNINGS.append(f"{where} — неизвестный тип `{t['type']}`")
    for key in ("created", "deadline", "closed"):
        if t.get(key) and not parse_date(t[key]):
            WARNINGS.append(f"{where} — не могу прочитать дату `{key}: {t[key]}` (нужно ГГГГ-ММ-ДД)")


def set_field(path: Path, key: str, value: str) -> None:
    """Меняет одно поле во frontmatter, сохраняя комментарии и порядок."""
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    lines = m.group(1).splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:", line):
            new = f"{key}: {value}".rstrip() if value else f"{key}:"
            cm = re.search(r"\s(#\s.*)$", line)
            if cm:
                new = new.ljust(20) + " " + cm.group(1)
            lines[i] = new
            break
    else:
        lines.append(f"{key}: {value}")
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + text[m.end():], encoding="utf-8")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


# ─────────────────────── файлы задач ───────────────────────
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n",
                     "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "",
                     "e", "yu", "ya"]))


def slugify(title: str, limit: int = 48) -> str:
    s = "".join(TRANSLIT.get(c, c) for c in title.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:limit].rstrip("-") or "task"


def all_task_files() -> list[Path]:
    files = sorted(TASKS.glob("*.md")) + sorted(ARCHIVE.glob("*/*.md"))
    return [f for f in files if f.name.lower() != "readme.md"]


def next_id(tasks: list[dict]) -> str:
    prefix = CONFIG.get("id_prefix", "BL")
    nums = [int(m.group(1)) for t in tasks if (m := re.match(rf"{prefix}-(\d+)", t.get("id", "")))]
    for f in all_task_files():  # на случай битых файлов
        if m := re.match(rf"{prefix}-(\d+)", f.name):
            nums.append(int(m.group(1)))
    return f"{prefix}-{(max(nums) if nums else 0) + 1:03d}"


def move(path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    path.rename(dest)
    return dest


def normalize(tasks: list[dict]) -> None:
    """Закрытые задачи — в архив с датой закрытия, переоткрытые — обратно в tasks/."""
    for t in tasks:
        p: Path = t["path"]
        in_archive = ARCHIVE in p.parents
        if t["status"] in CLOSED and not in_archive:
            if not parse_date(t.get("closed", "")):
                t["closed"] = today().isoformat()
                set_field(p, "closed", t["closed"])
            year = parse_date(t["closed"]).year
            t["path"] = move(p, ARCHIVE / str(year))
            print(f"→ в архив: {t['id']} ({STATUSES[t['status']][0]})")
        elif t["status"] not in CLOSED and in_archive:
            set_field(p, "closed", "")
            t["closed"] = ""
            t["path"] = move(p, TASKS)
            print(f"← из архива: {t['id']}")


def load_tasks() -> list[dict]:
    tasks = [t for f in all_task_files() if (t := parse_task(f))]
    seen: dict[str, Path] = {}
    for t in tasks:
        if t["id"] in seen:
            WARNINGS.append(f"Повторяется ID `{t['id']}`: `{rel(seen[t['id']])}` и `{rel(t['path'])}`")
        seen[t["id"]] = t["path"]
    return tasks


# ─────────────────────── отображение ───────────────────────
def days_left(t: dict) -> int | None:
    d = parse_date(t.get("deadline", ""))
    return (d - today()).days if d else None


def fmt_date(d: dt.date | None) -> str:
    if not d:
        return "—"
    return d.strftime("%d.%m") if d.year == today().year else d.strftime("%d.%m.%Y")


def deadline_cell(t: dict) -> str:
    d = parse_date(t.get("deadline", ""))
    if not d:
        return "—"
    n = (d - today()).days
    if n < 0:
        tail = f"🔴 просрочено на {-n} дн."
    elif n == 0:
        tail = "🔴 сегодня"
    elif n == 1:
        tail = "🟠 завтра"
    elif n <= CONFIG["hot_days"]:
        tail = f"🟠 через {n} дн."
    elif n <= CONFIG["soon_days"]:
        tail = f"🟡 через {n} дн."
    else:
        tail = f"через {n} дн."
    return f"{fmt_date(d)} · {tail}"


def sort_key(t: dict):
    n = days_left(t)
    return (PRIO_ORDER.index(t["priority"]), n is None, n if n is not None else 0,
            t.get("created", ""), t.get("id", ""))


def by_deadline(t: dict):
    n = days_left(t)
    return (n is None, n if n is not None else 0, PRIO_ORDER.index(t["priority"]))


def esc(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def link(t: dict, text: str | None = None) -> str:
    return f"[{esc(text or t['title'])}]({quote(rel(t['path']))})"


def prio(t: dict) -> str:
    name, _, icon = PRIORITIES[t["priority"]]
    return f"{icon} {name}"


def table(rows: list[dict], cols: list[str]) -> str:
    heads = {"id": "ID", "title": "Задача", "prio": "Приоритет", "deadline": "Срок",
             "type": "Тип", "system": "Система", "estimate": "Оценка",
             "initiator": "Инициатор", "status": "Статус", "project": "Проект"}
    out = ["| " + " | ".join(heads[c] for c in cols) + " |",
           "|" + "|".join(":--" for _ in cols) + "|"]
    for t in rows:
        cells = {
            "id": f"`{t['id']}`",
            "title": link(t),
            "prio": prio(t),
            "deadline": deadline_cell(t),
            "type": TYPES.get(t.get("type", ""), t.get("type", "") or "—"),
            "system": esc(t.get("system") or "—"),
            "estimate": esc(t.get("estimate") or "—"),
            "initiator": esc(t.get("initiator") or "—"),
            "status": " ".join(reversed(STATUSES[t["status"]])),
            "project": esc(PROJECTS.get(t.get("project", ""), {}).get("short", "—")),
        }
        out.append("| " + " | ".join(cells[c] for c in cols) + " |")
    return "\n".join(out)


def badge(label: str, value, color: str) -> str:
    enc = lambda s: quote(str(s).replace("-", "--").replace("_", "__").replace(" ", "_"))
    return f"![{label}: {value}](https://img.shields.io/badge/{enc(label)}-{enc(value)}-{color}?style=flat-square)"


def repo_name() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or CONFIG["repo"]


def dashboard_url() -> str:
    owner, name = repo_name().split("/")
    return f"https://{owner.lower()}.github.io/{name}/"


def render_readme(tasks: list[dict]) -> str:
    active = [t for t in tasks if t["status"] not in CLOSED]
    closed = [t for t in tasks if t["status"] in CLOSED]
    hot = sorted([t for t in active if t["status"] != "inbox" and (n := days_left(t)) is not None
                  and n <= CONFIG["hot_days"] and t["priority"] != "wont"], key=by_deadline)
    in_work = sorted([t for t in active if t["status"] == "in-progress"], key=sort_key)
    waiting = sorted([t for t in active if t["status"] == "waiting"], key=by_deadline)
    inbox = sorted([t for t in active if t["status"] == "inbox"], key=lambda t: t.get("created", ""))
    backlog = [t for t in active if t["status"] == "backlog"]
    month_ago = today() - dt.timedelta(days=30)
    done_30 = [t for t in closed if t["status"] == "done"
               and (d := parse_date(t.get("closed", ""))) and d >= month_ago]
    repo = repo_name()

    L: list[str] = []
    L += ['<div align="center">', "", f"# {CONFIG['title']}", "", f"{CONFIG['subtitle']}", ""]
    L += [f"**[Дашборд]({dashboard_url()})** · [Архив](archive/README.md) · "
          f"[Как вести бэклог](docs/GUIDE.md) · "
          f"[＋ Новая задача](https://github.com/{repo}/issues/new?template=task.yml)", ""]
    L += [" ".join([
        badge("в работе", len(in_work), "7aa2f7"),
        badge("горит", len(hot), "f7768e" if hot else "414868"),
        badge("Must", sum(t["priority"] == "must" for t in active), "e06c75"),
        badge("всего активных", len(active), "565f89"),
        badge("закрыто за 30 дн", len(done_30), "9ece6a"),
    ]), ""]
    L += [f"<sub>Сводка собрана автоматически {today().strftime('%d.%m.%Y')} · "
          "не редактируйте этот файл вручную</sub>", "", "</div>", ""]

    if hot:
        L += ["> [!CAUTION]", "> **Горит — срок истёк или наступает в ближайшие "
              f"{CONFIG['hot_days']} дн.**", ">"]
        L += [f"> - `{t['id']}` {link(t)} — {deadline_cell(t)}" for t in hot]
        L += [""]

    if PROJECTS:
        L += ["## 📁 Проекты", "", "| Проект | Прогресс | Активных | Ближайший срок | Страница проекта |",
              "|:--|:--|:--|:--|:--|"]
        for key, pr in PROJECTS.items():
            mine = [t for t in tasks if t.get("project") == key]
            done = sum(t["status"] == "done" for t in mine)
            total = sum(t["status"] != "canceled" for t in mine)
            act = [t for t in mine if t["status"] not in CLOSED]
            nxt = sorted([t for t in act if days_left(t) is not None], key=by_deadline)
            pct = round(100 * done / total) if total else 0
            bar = "▰" * round(pct / 10) + "▱" * (10 - round(pct / 10))
            near = f"`{nxt[0]['id']}` {deadline_cell(nxt[0])}" if nxt else "—"
            L.append(f"| **{esc(pr['name'])}** | `{bar}` {done}/{total} | {len(act)} | "
                     f"{near} | [открыть ↗]({pr['url']}) |")
        L += [""]

    L += ["## 🎯 В работе", ""]
    L += [table(in_work, ["id", "title", "prio", "deadline", "project", "estimate"]) if in_work
          else "_Сейчас ничего не в работе — возьмите задачу из Must._", ""]

    L += ["## 🗂 Бэклог по приоритету (MoSCoW)", "",
          "<sub>Внутри группы — по сроку: просроченные и ближайшие сверху, без срока — в конце.</sub>", ""]
    for key, (name, hint, icon) in PRIORITIES.items():
        rows = sorted([t for t in backlog if t["priority"] == key], key=sort_key)
        head = f"### {icon} {name} — {hint} · {len(rows)}"
        body = table(rows, ["id", "title", "deadline", "project", "type", "system"]) if rows else "_Пусто_"
        if key == "wont":
            L += [f"<details><summary><b>{icon} {name} — {hint} · {len(rows)}</b></summary>", "", body, "",
                  "</details>", ""]
        else:
            L += [head, "", body, ""]

    L += [f"## ⏳ Ожидание · {len(waiting)}", "",
          "<sub>Ждём ответа, данных или решения от других.</sub>", ""]
    L += [table(waiting, ["id", "title", "prio", "deadline", "project"]) if waiting else "_Пусто_", ""]

    L += [f"## 📥 Входящие · {len(inbox)}", "",
          "<sub>Ещё не разобраны: нужно уточнить суть, приоритет и срок, затем перевести в `backlog`.</sub>", ""]
    L += [table(inbox, ["id", "title", "prio", "deadline", "initiator"]) if inbox else "_Всё разобрано_ ✨", ""]

    if WARNINGS:
        L += ["---", "", "<details><summary>⚠️ Замечания к оформлению задач · "
              f"{len(WARNINGS)}</summary>", ""] + [f"- {w}" for w in WARNINGS] + ["", "</details>", ""]
    return "\n".join(L)


def render_archive(tasks: list[dict]) -> str:
    closed = [t for t in tasks if t["status"] in CLOSED]
    closed.sort(key=lambda t: (t.get("closed", ""), t.get("id", "")), reverse=True)
    L = ["# 📦 Архив задач", "", "[← К бэклогу](../README.md)", "",
         f"Закрыто задач: **{len(closed)}** · выполнено: "
         f"**{sum(t['status'] == 'done' for t in closed)}** · отменено: "
         f"**{sum(t['status'] == 'canceled' for t in closed)}**", "",
         "<sub>Задачи из архива не удаляются. Чтобы вернуть задачу в работу, "
         "поменяйте ей `status` — она сама переедет обратно в `tasks/`.</sub>", ""]
    months: dict[str, list[dict]] = {}
    names = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
             "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    for t in closed:
        d = parse_date(t.get("closed", ""))
        months.setdefault(f"{names[d.month]} {d.year}" if d else "Без даты", []).append(t)
    for month, rows in months.items():
        L += [f"## {month} · {len(rows)}", "",
              "| ID | Задача | Итог | Закрыта | Срок был | Тип |", "|:--|:--|:--|:--|:--|:--|"]
        for t in rows:
            path = quote(t["path"].relative_to(ARCHIVE).as_posix())
            L.append(f"| `{t['id']}` | [{esc(t['title'])}]({path}) | "
                     f"{' '.join(reversed(STATUSES[t['status']]))} | "
                     f"{fmt_date(parse_date(t.get('closed', '')))} | "
                     f"{fmt_date(parse_date(t.get('deadline', '')))} | "
                     f"{TYPES.get(t.get('type', ''), '—')} |")
        L.append("")
    if not closed:
        L += ["_Пока пусто._", ""]
    return "\n".join(L)


def write_data(tasks: list[dict]) -> None:
    SITE.mkdir(exist_ok=True)
    data = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "repo": repo_name(),
        "config": {k: CONFIG[k] for k in ("title", "subtitle", "hot_days", "soon_days")},
        "projects": PROJECTS,
        "labels": {"status": {k: v[0] for k, v in STATUSES.items()},
                   "priority": {k: [v[0], v[1]] for k, v in PRIORITIES.items()},
                   "type": TYPES},
        "warnings": WARNINGS,
        "tasks": [{**{k: t.get(k, "") for k in FIELDS}, "path": rel(t["path"]), "body": t["body"]}
                  for t in sorted(tasks, key=sort_key)],
    }
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    (SITE / "index.html").write_text(html, encoding="utf-8")
    (SITE / ".nojekyll").write_text("")


def build() -> None:
    TASKS.mkdir(exist_ok=True)
    ARCHIVE.mkdir(exist_ok=True)
    tasks = load_tasks()
    normalize(tasks)
    (ROOT / "README.md").write_text(render_readme(tasks), encoding="utf-8")
    (ARCHIVE / "README.md").write_text(render_archive(tasks), encoding="utf-8")
    write_data(tasks)
    active = sum(t["status"] not in CLOSED for t in tasks)
    print(f"Готово: активных {active}, в архиве {len(tasks) - active}, замечаний {len(WARNINGS)}")
    for w in WARNINGS:
        print("  ⚠", w)


# ─────────────────────── создание задач ───────────────────────
def create_task(title: str, priority="should", type_="", system="", initiator="", deadline="",
                estimate="", bitrix="", status="inbox", tags=None, body="", project="") -> Path:
    tasks = load_tasks()
    WARNINGS.clear()
    tid = next_id(tasks)
    d = parse_date(deadline)
    values = {
        "id": tid, "title": title.strip(), "status": status, "priority": priority,
        "type": ALIASES["type"].get(type_.lower(), type_.lower()), "project": project, "system": system,
        "initiator": initiator, "created": today().isoformat(),
        "deadline": d.isoformat() if d else "", "estimate": estimate, "bitrix": bitrix,
        "tags": "[" + ", ".join(tags or []) + "]", "closed": "",
    }
    path = TASKS / f"{tid}-{slugify(title)}.md"
    path.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    for k, v in values.items():
        set_field(path, k, v)
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    tail = text[m.end():]
    if body:
        tail = body.strip() + "\n\n## Журнал\n- " + today().isoformat() + " — задача заведена\n"
    else:
        tail = tail.replace("2026-01-01", today().isoformat())
    path.write_text(text[:m.end()] + "\n" + tail.lstrip("\n"), encoding="utf-8")
    print(f"Создана задача {tid}: {rel(path)}")
    return path


ISSUE_FIELDS = {
    "название": "title", "суть и контекст": "body", "приоритет": "priority", "тип": "type",
    "система": "system", "инициатор": "initiator", "срок": "deadline", "оценка": "estimate",
    "ссылка на битрикс": "bitrix", "статус": "status", "проект": "project",
}


def from_issue(event_path: str) -> None:
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    issue = event["issue"]
    parts = re.split(r"^###\s+", issue.get("body") or "", flags=re.M)
    vals: dict[str, str] = {}
    for part in parts:
        if not part.strip():
            continue
        head, _, value = part.partition("\n")
        key = ISSUE_FIELDS.get(head.strip().lower())
        value = value.strip()
        if key and value and value != "_No response_":
            vals[key] = value
    pick = lambda v, table: next((k for k in table if v and v.lower().startswith(k)), "")
    prio_raw = vals.get("priority", "Should").lower().replace("won't", "wont")
    type_raw = vals.get("type", "")
    type_key = next((k for k, name in TYPES.items() if type_raw.lower().startswith(name.lower())), "")
    proj = next((k for k, p in PROJECTS.items() if vals.get("project", "").strip() == p["name"]), "")
    status = "backlog" if vals.get("status", "").lower().startswith("бэклог") else "inbox"
    body = vals.get("body", "")
    body = f"## Суть\n{body}\n\n## Что сделать\n- [ ] \n\n## Критерии готовности\n- \n\n## Материалы\n- Создано из [issue #{issue['number']}]({issue['html_url']})"
    path = create_task(
        title=vals.get("title") or issue["title"].removeprefix("[Задача]").strip(),
        priority=pick(prio_raw, PRIORITIES) or "should", type_=type_key,
        system=vals.get("system", ""), initiator=vals.get("initiator", ""),
        deadline=vals.get("deadline", ""), estimate=vals.get("estimate", ""),
        bitrix=vals.get("bitrix", ""), status=status, body=body, project=proj,
    )
    tid = path.name.split("-")[0] + "-" + path.name.split("-")[1]
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"task_id={tid}\ntask_path={rel(path)}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("new")
    n.add_argument("--title", required=True)
    for opt in ("priority", "type", "project", "system", "initiator", "deadline", "estimate", "bitrix", "status"):
        n.add_argument(f"--{opt}", default="")
    n.add_argument("--tags", default="")
    fi = sub.add_parser("from-issue")
    fi.add_argument("event")
    args = ap.parse_args()
    if args.cmd == "new":
        create_task(args.title, args.priority or "should", args.type, args.system, args.initiator,
                    args.deadline, args.estimate, args.bitrix, args.status or "inbox",
                    [t.strip() for t in args.tags.split(",") if t.strip()], project=args.project)
    elif args.cmd == "from-issue":
        from_issue(args.event)
    build()


if __name__ == "__main__":
    sys.exit(main())
