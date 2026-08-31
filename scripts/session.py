"""Session manager CLI for the ielts-vocab skill (stdlib only).

Subcommands:
    plan [--list LIST] [--new N] [--due N] [--csv]
        Print today's study plan: due reviews + new words.
        Defaults come from settings.json (see `config`); CLI flags override once.
        Overload protection: when due backlog > threshold and auto_reduce is on,
        new words shrink proportionally (threshold / backlog). Output field
        "adjusted" explains any reduction; "backlog" is the full due count.
        Exit code 1 when nothing is due (agent can say so directly).
    words --ids a,b,c [--lang en|cn|all]
        Print full word entries for the given ids.
    record --results id=grade,id=grade
        Apply grades (again/hard/good/easy), update user_data.json atomically.
        Example: record --results habitat=good,deposit=again
    stats
        Study statistics: coverage, mastery, streak, due today.
    config [--new N] [--due N] [--auto-reduce 0|1] [--threshold N] [--reset]
        View or persist daily-load settings to settings.json. These become the
        defaults for `plan`; existing user_data.json data is never touched.
    add --word WORD --cn MEANING [--pos n.] [--phonetic /../] [--sentence ...]
        [--sentence-cn ...] [--list custom] [--paraphrase "a, b"]
        Add a word to the custom wordlist; merge if id exists.
    serve [--port N]
        Run a local HTTP bridge (127.0.0.1) so card pages can POST /record
        and save grades directly -- no sendPrompt / clipboard needed.
        Endpoints: GET /ping, GET|POST /plan (today's remaining plan),
        POST /record (applies grades; response embeds updated "remaining"
        plan so card pages can offer a relearn round in-session).
    ping
        Probe whether the bridge is running; prints its base URL.

Paths: data dir = ../assets/data relative to this file; user_data.json lives there.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "assets", "data"))
USER_FILE = os.path.join(DATA_DIR, "user_data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CUSTOM_LIST = "wordlist_custom.json"

DEFAULT_SETTINGS = {
    "new_per_day": 10,
    "max_due_per_day": 60,
    "auto_reduce": True,
    "reduce_threshold": 50,
}


# ---------- settings ----------

def load_settings() -> dict:
    """Merge settings.json over DEFAULT_SETTINGS; unknown keys preserved."""
    out = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                out.update(json.load(f))
        except (ValueError, OSError):
            pass
    return out


def save_settings(s: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

sys.path.insert(0, HERE)
import scheduler  # noqa: E402


# ---------- persistence ----------

def load_user() -> dict:
    if os.path.exists(USER_FILE):
        with open(USER_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "created": _dt.date.today().isoformat(), "words": {}, "history": {}}


def save_user(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, USER_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def all_lists() -> dict:
    out = {}
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.startswith("wordlist_") and fn.endswith(".json"):
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            out[d["meta"]["list_id"]] = d
    return out


def load_words() -> dict:
    """word_id -> entry, with 'list' attached."""
    out = {}
    for list_id, d in all_lists().items():
        for w in d["words"]:
            w2 = dict(w)
            w2["list"] = list_id
            out[w2["id"]] = w2
    return out


# ---------- plan ----------

def build_plan(new_limit: int | None = None, due_limit: int | None = None,
               list_filter: str | None = None) -> dict:
    """Compute today's plan dict. Pure read; reusable by CLI and HTTP bridge."""
    user = load_user()
    words = load_words()
    settings = load_settings()
    states = user.get("words", {})
    today_iso = _dt.date.today().isoformat()

    due_ids = [
        wid for wid, st in states.items()
        if wid in words and scheduler.is_due(st)
    ]
    due_ids.sort(key=lambda w: (states[w].get("due", ""), w))

    if list_filter:
        due_ids = [wid for wid in due_ids if words[wid]["list"] == list_filter]

    new_limit = settings["new_per_day"] if new_limit is None else new_limit
    due_limit = settings["max_due_per_day"] if due_limit is None else due_limit
    backlog = len(due_ids)

    # overload protection: due backlog above threshold -> shrink new words
    # proportionally (Anki-style); never drop below 5 unless backlog is huge.
    adjusted = None
    if settings["auto_reduce"] and backlog > settings["reduce_threshold"] and new_limit > 0:
        ratio = settings["reduce_threshold"] / backlog
        reduced = max(5, round(new_limit * ratio))
        if reduced < new_limit:
            adjusted = {
                "planned_new": new_limit, "new": reduced,
                "backlog": backlog, "threshold": settings["reduce_threshold"],
            }
            new_limit = reduced

    # words already introduced today (from history) -> don't double-issue
    introduced_today = user.get("history", {}).get(today_iso, {}).get("new", 0)
    remaining_new = new_limit - introduced_today
    new_pool = [wid for wid in words if wid not in states]
    if list_filter:
        new_pool = [wid for wid in new_pool if words[wid]["list"] == list_filter]
    new_ids = new_pool[: max(0, remaining_new)]

    out = {
        "date": today_iso,
        "due_reviews": due_ids[:due_limit],
        "new_words": new_ids,
        "backlog": backlog,
    }
    if adjusted:
        out["adjusted"] = adjusted
    return out


def cmd_plan(args) -> int:
    out = build_plan(
        new_limit=args.new, due_limit=args.due, list_filter=args.list,
    )
    if args.csv:
        print(",".join(out["due_reviews"] + out["new_words"]))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if (out["due_reviews"] or out["new_words"]) else 1


# ---------- words ----------

def cmd_words(args) -> int:
    words = load_words()
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    sel = []
    for i in ids:
        if i not in words:
            print(json.dumps({"error": f"unknown id: {i}"}, ensure_ascii=False))
            return 2
        sel.append(words[i])
    if args.lang == "en":
        for w in sel:
            w = {k: v for k, v in w.items() if k not in ("cn", "sentence_cn")}
            print(json.dumps(w, ensure_ascii=False))
    elif args.lang == "cn":
        for w in sel:
            w = {k: v for k, v in w.items() if k in ("id", "word", "cn", "sentence_cn")}
            print(json.dumps(w, ensure_ascii=False))
    else:
        print(json.dumps(sel, ensure_ascii=False, indent=1))
    return 0


# ---------- record ----------

def apply_results(results_str: str) -> dict:
    """Parse 'id=grade,id=grade', apply SM-2, save atomically.

    Returns {"ok": bool, "recorded": [...], "errors": [...]}.
    """
    user = load_user()
    words = load_words()
    today_iso = _dt.date.today().isoformat()
    hist = user.setdefault("history", {}).setdefault(today_iso, {"reviews": 0, "correct": 0, "new": 0})
    recorded, errors = [], []

    for pair in results_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        wid, _, grade = pair.partition("=")
        if wid not in words:
            errors.append({"id": wid, "error": f"unknown id: {wid}"})
            continue
        prev = user["words"].get(wid, scheduler.new_state())
        was_new = prev.get("repetitions", 0) == 0 and wid not in user["words"]
        try:
            st = scheduler.review(prev, grade)
        except ValueError as e:
            errors.append({"id": wid, "error": str(e)})
            continue
        user["words"][wid] = st
        hist["reviews"] += 1
        if st.get("last_grade", 0) >= 3:
            hist["correct"] += 1
        if was_new:
            hist["new"] += 1
        recorded.append({
            "id": wid, "grade": grade,
            "easiness": st["easiness"], "interval": st["interval"],
            "due": st["due"], "lapses": st["lapses"],
            "leech": scheduler.is_leech(st),
        })

    save_user(user)
    return {"ok": not errors, "recorded": recorded, "errors": errors}


def cmd_record(args) -> int:
    out = apply_results(args.results)
    for r in out["recorded"]:
        print(json.dumps(r, ensure_ascii=False))
    for e in out["errors"]:
        print(json.dumps({"error": e["error"]}, ensure_ascii=False))
    return 0 if out["ok"] else 2


def _log_beacon(tag: str) -> None:
    """Append probe hits to a log file so the agent can verify which channel worked."""
    import io as _io
    path = os.path.join(DATA_DIR, "beacon_log.txt")
    try:
        with _io.open(path, "a", encoding="utf-8") as f:
            f.write(_dt.datetime.now().isoformat(timespec="seconds") + " " + tag + "\n")
    except OSError:
        pass


# ---------- serve (local bridge for card pages) ----------

def cmd_serve(args) -> int:
    """Run a tiny local HTTP bridge so card pages can POST results directly.

    GET  /ping            -> {"ok": true}
    POST /record          -> body {"results": "id=grade,..."} applies grades, saves
    Server binds 127.0.0.1 only; auto-picks a free port in [SERVER_PORT_RANGE].
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            from urllib.parse import urlsplit, parse_qs
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            if u.path == "/ping":
                self._send(200, {"ok": True, "date": _dt.date.today().isoformat()})
            elif u.path == "/plan":
                self._send(200, {"ok": True, **build_plan()})
            elif u.path == "/beacon":
                tag = (q.get("tag") or [""])[0]
                _log_beacon("GET " + tag)
                self._send(200, {"ok": True, "tag": tag})
            elif u.path == "/probe.js":
                tag = (q.get("tag") or [""])[0]
                _log_beacon("JS " + tag)
                body = b"var __probe_ok=1;"
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/record-js":
                import re as _re
                results = (q.get("results") or [""])[0]
                cb = _re.sub(r"[^A-Za-z0-9_$.]", "", (q.get("cb") or ["__cb"])[0]) or "__cb"
                out = apply_results(results) if results else {"ok": False, "error": "empty"}
                body = (cb + "(" + json.dumps(out, ensure_ascii=False) + ");").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if self.path == "/record":
                try:
                    n = int(self.headers.get("Content-Length", "0") or 0)
                    raw = self.rfile.read(n).decode("utf-8") or "{}"
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        data = {"results": raw.strip()}
                    results = (data.get("results") or "").strip()
                    if not results:
                        self._send(400, {"ok": False, "error": "empty results"})
                        return
                    out = apply_results(results)
                    plan = build_plan()
                    self._send(200 if out["ok"] else 422, {
                        **out,
                        "remaining": {
                            "due_reviews": plan["due_reviews"],
                            "new_words": plan["new_words"],
                            "backlog": plan["backlog"],
                        },
                    })
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"ok": False, "error": str(e)})
            elif self.path == "/plan":
                try:
                    n = int(self.headers.get("Content-Length", "0") or 0)
                    if n:
                        self.rfile.read(n)
                    plan = build_plan()
                    self._send(200, {"ok": True, **plan})
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"ok": False, "error": str(e)})
            else:
                self._send(404, {"ok": False, "error": "not found"})

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):  # silence per-request stderr noise
            pass

    port = args.port
    if port == 0:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    try:
        srv = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"cannot bind port {port}: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "bridge": f"http://127.0.0.1:{port}"}, ensure_ascii=False), flush=True)
    try:
        with open(os.path.join(DATA_DIR, "bridge_port.txt"), "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_ping(args) -> int:
    """Check whether the local bridge is alive (for agents to probe first)."""
    import urllib.request

    candidates = _bridge_candidates()
    for base in candidates:
        try:
            with urllib.request.urlopen(base + "/ping", timeout=1) as r:
                if json.loads(r.read().decode("utf-8")).get("ok"):
                    print(json.dumps({"ok": True, "bridge": base}))
                    return 0
        except Exception:
            continue
    print(json.dumps({"ok": False, "candidates": candidates}))
    return 1


def _bridge_candidates() -> list:
    out = []
    port_file = os.path.join(DATA_DIR, "bridge_port.txt")
    if os.path.exists(port_file):
        with open(port_file, encoding="utf-8") as f:
            p = f.read().strip()
        if p.isdigit():
            out.append(f"http://127.0.0.1:{p}")
    for p in (8765, 8766, 8767):
        base = f"http://127.0.0.1:{p}"
        if base not in out:
            out.append(base)
    return out


# ---------- config ----------

def cmd_config(args) -> int:
    if args.reset:
        save_settings(dict(DEFAULT_SETTINGS))
        print(json.dumps({"ok": True, "reset": True, **DEFAULT_SETTINGS}, ensure_ascii=False, indent=1))
        return 0
    s = load_settings()
    changed = {}
    if args.new is not None:
        s["new_per_day"] = max(0, args.new)
        changed["new_per_day"] = s["new_per_day"]
    if args.due is not None:
        s["max_due_per_day"] = max(1, args.due)
        changed["max_due_per_day"] = s["max_due_per_day"]
    if args.auto_reduce is not None:
        s["auto_reduce"] = bool(args.auto_reduce)
        changed["auto_reduce"] = s["auto_reduce"]
    if args.threshold is not None:
        s["reduce_threshold"] = max(1, args.threshold)
        changed["reduce_threshold"] = s["reduce_threshold"]
    if changed:
        save_settings(s)
        print(json.dumps({"ok": True, "changed": changed, "settings": s}, ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"ok": True, "settings": s}, ensure_ascii=False, indent=1))
    return 0


# ---------- stats ----------

def cmd_stats(args) -> int:
    user = load_user()
    words = load_words()
    states = user.get("words", {})
    total = len(words)
    seen = [wid for wid in states if wid in words]
    mastered = [wid for wid in seen if states[wid].get("repetitions", 0) >= 2 and states[wid].get("interval", 0) >= 7]
    learning = [wid for wid in seen if wid not in mastered]
    due_today = [wid for wid in seen if scheduler.is_due(states[wid])]

    hist = user.get("history", {})
    streak = 0
    d = _dt.date.today()
    while hist.get(d.isoformat(), {}).get("reviews", 0) > 0:
        streak += 1
        d -= _dt.timedelta(days=1)

    leeches = [wid for wid in seen if scheduler.is_leech(states[wid])]

    out = {
        "date": _dt.date.today().isoformat(),
        "total_words": total,
        "seen": len(seen),
        "mastered": len(mastered),
        "learning": len(learning),
        "untouched": total - len(seen),
        "due_today": len(due_today),
        "due_ids_preview": due_today[:8],
        "streak_days": streak,
        "leeches": leeches[:10],
        "lists": {
            lid: {"title": d["meta"]["title"], "words": d["meta"]["word_count"]}
            for lid, d in all_lists().items()
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


# ---------- add ----------

def cmd_add(args) -> int:
    wid = args.word.strip().lower()
    path = os.path.join(DATA_DIR, f"wordlist_{args.list}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {
            "meta": {
                "list_id": args.list, "title": "自定义词库", "description": "学习过程中添加的词",
                "version": "1.0.0", "word_count": 0,
            },
            "words": [],
        }

    words_all = load_words()
    if wid in words_all:
        print(json.dumps({"error": f"already exists in list '{words_all[wid]['list']}': {wid}"}, ensure_ascii=False))
        return 2

    entry = {
        "id": wid, "word": args.word.strip(), "pos": args.pos or "", "cn": args.cn,
        "phonetic": args.phonetic or "", "sentence": args.sentence or "",
        "sentence_cn": args.sentence_cn or "",
        "paraphrase": [p.strip() for p in (args.paraphrase or "").split(",") if p.strip()],
        "tags": [args.list],
    }
    for w in data["words"]:
        if w["id"] == wid:
            data["words"].append(entry)
            break
    else:
        data["words"].append(entry)
    data["meta"]["word_count"] = len(data["words"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(json.dumps({"added": wid, "list": args.list, "total": data["meta"]["word_count"]}, ensure_ascii=False))
    return 0


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(prog="session.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="today's study plan")
    p.add_argument("--list", help="filter by list id")
    p.add_argument("--new", type=int, default=None, help="new words per day (default: settings.json)")
    p.add_argument("--due", type=int, default=None, help="max due reviews (default: settings.json)")
    p.add_argument("--csv", action="store_true", help="print ids as csv only")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser(
        "config",
        help="view or persist daily-load settings (settings.json)",
    )
    p.add_argument("--new", type=int, help="new words per day")
    p.add_argument("--due", type=int, help="max due reviews per day")
    p.add_argument("--auto-reduce", dest="auto_reduce", type=int, choices=[0, 1], help="auto-reduce new words on backlog (1/0)")
    p.add_argument("--threshold", type=int, help="backlog threshold triggering reduction")
    p.add_argument("--reset", action="store_true", help="restore defaults")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("words", help="print word entries")
    p.add_argument("--ids", required=True, help="comma-separated word ids")
    p.add_argument("--lang", choices=["en", "cn", "all"], default="all")
    p.set_defaults(func=cmd_words)

    p = sub.add_parser("record", help="record grades")
    p.add_argument("--results", required=True, help="id=grade,id=grade (again/hard/good/easy)")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("serve", help="run local HTTP bridge for card pages")
    p.add_argument("--port", type=int, default=0, help="0 = auto-pick free port (default)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ping", help="check whether local bridge is alive")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("stats", help="study statistics")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("add", help="add word to custom list")
    p.add_argument("--word", required=True)
    p.add_argument("--cn", required=True)
    p.add_argument("--pos", default="")
    p.add_argument("--phonetic", default="")
    p.add_argument("--sentence", default="")
    p.add_argument("--sentence-cn", dest="sentence_cn", default="")
    p.add_argument("--paraphrase", default="")
    p.add_argument("--list", default="custom")
    p.set_defaults(func=cmd_add)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
