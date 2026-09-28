"""DWthon Tools - Poranny Briefing.

Uruchom z notebooka Dnia 3:
    python app.py

Interfejs nie wysyła, nie usuwa i nie przesuwa niczego w świecie zewnętrznym.
Pokazuje wyłącznie stan przygotowany przez helper.py i zapisuje decyzje w audycie.
"""
from __future__ import annotations

import html
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import gradio as gr

import helper as h
from dwthon_google_fake import WorkspaceClient

PORT = int(os.getenv("TOOLS_BRIEFING_PORT", "8080"))
_SERVICE_PREFIX = os.getenv("JUPYTERHUB_SERVICE_PREFIX", "").rstrip("/")
ROOT_PATH = f"{_SERVICE_PREFIX}/proxy/{PORT}" if _SERVICE_PREFIX else ""

PROFILES = {
    "founder": "Zwykły poranek: klient, follow-up i podejrzany wątek.",
    "team_lead": "Poranek osoby prowadzącej zespół.",
    "quiet_day": "Pusty poranek: test, czy robot umie nie wymyślać pracy.",
}
LAYERS = [
    ("1", "🗂️", "Registry", "co istnieje"),
    ("2", "🎯", "Decision", "structured output"),
    ("3", "🚧", "Policy", "czy wolno"),
    ("4", "⚙️", "Router", "kto wykonuje"),
    ("5", "🧼", "Normalization", "co wraca"),
    ("6", "🧑‍⚖️", "Human Gate", "ostatnia sekunda"),
    ("7", "🧾", "Audit", "dlaczego"),
]
WORLD_CHANGING = {"send_email", "calendar_move", "delete_thread"}


def blank_state() -> dict[str, Any]:
    return {
        "ran": False, "profile": "founder", "mode": "controlled", "policy": None,
        "briefing": None, "evidence": [], "budget": {}, "audit": [], "tool_log": [],
        "gate": {"state": "idle", "reason": "", "payload": {}, "kind": "", "evidence_ids": []},
        "raw": "", "clean": "", "status": "Kliknij „Uruchom poranek”.",
    }


RUN: dict[str, Any] = blank_state()


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def audit(layer: int, event: str, detail: str = "", actor: str = "system") -> None:
    RUN["audit"].insert(0, {"at": now(), "layer": layer, "event": event, "detail": detail, "actor": actor})
    RUN["audit"] = RUN["audit"][:30]


def log_tool(name: str, state: str, reason: str) -> None:
    RUN["tool_log"].insert(0, {"name": name, "state": state, "reason": reason})
    RUN["tool_log"] = RUN["tool_log"][:16]


def needs_human(action: dict[str, Any], sources: list[dict[str, Any]]) -> tuple[bool, str]:
    """Reguła z Zadania 2.3: app.py używa dokładnie tej granicy."""
    if action.get("kind") in WORLD_CHANGING:
        return True, "akcja zmienia świat"
    if not sources:
        return True, "brak evidence - to zgadywanie, nie wniosek"
    if any(source.get("suspicious") for source in sources):
        return True, "źródło jest oflagowane jako nieufne"
    return False, "odwracalna akcja na czystych źródłach"


def controlled(profile: str, max_results: int, max_body_chars: int, autonomy: str) -> None:
    RUN.update(blank_state())
    RUN.update(profile=profile, mode="controlled")
    mode = h.AutonomyMode.AUTO_ELIGIBLE if autonomy == h.AutonomyMode.AUTO_ELIGIBLE.value else h.AutonomyMode.APPROVAL_REQUIRED
    policy = h.BriefingPolicy(max_results=int(max_results), max_body_chars=int(max_body_chars), autonomy_mode=mode)
    RUN["policy"] = policy
    client = WorkspaceClient(profile=profile)
    snapshot = h.fetch_snapshot(client, policy)
    threads = snapshot.get("gmail", {}).get("threads", [])
    known = {item.get("id") for item in threads if item.get("id")}

    audit(1, "catalog_exposed", f"{len(policy.allowed_tools)} zdolności read-only")
    audit(3, "scope_applied", f"{policy.gmail_query}; max_results={policy.max_results}")
    log_tool("search_gmail", "ok", "allow-lista, zakres i limit z policy")
    log_tool("today_calendar", "ok", "read-only")

    raw_threads = []
    for item in threads:
        call = h.ToolCall(tool=h.ToolName.GET_THREAD, arguments={"thread_id": item["id"]})
        allowed, reason = h.is_allowed(call, policy, known)
        if not allowed:
            log_tool("get_thread", "blocked", reason)
            audit(3, "tool_call_blocked", reason)
            continue
        full = client.get_thread(item["id"], full=True)
        raw_threads.append(full)
        log_tool("get_thread", "ok", "ID pochodzi z wyniku wyszukiwania")

    evidence, dropped = h.collect_evidence(snapshot, policy)
    raw = {"snapshot": snapshot, "threads": raw_threads}
    budget = h.context_budget(raw, evidence)
    budget["items_dropped"] = dropped
    RUN.update(evidence=evidence, budget=budget, raw=json.dumps(raw, ensure_ascii=False, indent=2))
    RUN["clean"] = "\n".join(item.model_dump_json() for item in evidence)
    audit(5, "context_normalized", f"{budget['raw_chars']} → {budget['context_chars']} znaków; evidence={len(evidence)}")
    for item in evidence:
        if item.suspicious:
            audit(5, "untrusted_content_flagged", f"{item.id}: widoczne, bez prawa do akcji")

    briefing = h.build_briefing(profile, snapshot, policy)
    RUN["briefing"] = briefing
    audit(2, "briefing_built", f"{len(briefing.priorities)} priorytety, {len(briefing.drafts)} drafty")
    action = briefing.action_preview
    ids = set(action.evidence_ids)
    sources = [{"suspicious": item.suspicious} for item in evidence if item.id in ids]
    gate_needed, reason = needs_human({"kind": action.kind}, sources)
    state = "auto" if policy.autonomy_mode == h.AutonomyMode.AUTO_ELIGIBLE and not gate_needed else "waiting"
    RUN["gate"] = {"state": state, "reason": reason, "payload": action.payload, "kind": action.kind,
                   "evidence_ids": list(ids)}
    audit(6, f"human_gate_{state}", f"{action.kind}: {reason}")
    audit(7, "run_completed", "wysłane maile=0")
    RUN["ran"] = True
    RUN["status"] = "✅ Poranek gotowy. Robot przygotował stan, ale niczego nie wysłał."


def naive(profile: str) -> None:
    """Celowo deterministyczna demonstracja systemu bez warstw ochronnych."""
    RUN.update(blank_state())
    RUN.update(profile=profile, mode="naive", ran=True)
    client = WorkspaceClient(profile=profile)
    snapshot = client.morning_snapshot(gmail_query="in:inbox newer_than:14d", max_emails=10)
    raw_threads = [client.get_thread(item["id"], full=True) for item in snapshot.get("gmail", {}).get("threads", [])]
    raw = json.dumps({"snapshot": snapshot, "threads": raw_threads}, ensure_ascii=False, indent=2)
    RUN["raw"] = raw
    RUN["clean"] = "Brak normalizacji. Cały surowy wynik trafia do modelu."
    RUN["budget"] = {"raw_chars": len(raw), "context_chars": len(raw), "reduction_pct": 0,
                     "raw_tokens_approx": len(raw)//4, "context_tokens_approx": len(raw)//4,
                     "items_used": 0, "items_dropped": 0}
    log_tool("search_gmail", "warn", "brak policy - szeroki zakres")
    log_tool("send_email", "executed", "symulacja: system wykonał propozycję bez zgody")
    RUN["gate"] = {"state": "sent", "reason": "warstwa 6 nie istnieje", "kind": "send_email",
                   "payload": {"to": "external@example.test", "subject": "Automatyczna odpowiedź", "body": "Symulowany mail z trybu naiwnego."},
                   "evidence_ids": []}
    RUN["status"] = "🔴 Tryb NAIWNY: nie ma policy, normalizacji, human gate ani audytu."


def layer_cards() -> str:
    if not RUN["ran"]:
        states = {key: ("off", "—") for key, *_ in LAYERS}
    elif RUN["mode"] == "naive":
        states = {"1": ("warn", "bez kuracji"), "2": ("warn", "proza"), "3": ("dead", "BRAK"),
                  "4": ("warn", "auto-exec"), "5": ("dead", "BRAK"), "6": ("dead", "BRAK"), "7": ("dead", "BRAK")}
    else:
        blocked = sum(row["state"] == "blocked" for row in RUN["tool_log"])
        flagged = sum(item.suspicious for item in RUN["evidence"])
        gate = RUN["gate"]["state"]
        states = {"1": ("ok", "3 read-only"), "2": ("ok", "Pydantic"),
                  "3": ("block" if blocked else "ok", f"{blocked} blokad" if blocked else "w zakresie"),
                  "4": ("ok", "router"), "5": ("flag" if flagged else "ok", f"{flagged} oflag."),
                  "6": ("wait" if gate == "waiting" else "ok", "czeka na Ciebie" if gate == "waiting" else gate),
                  "7": ("ok", f"{len(RUN['audit'])} wpisów")}
    cards = []
    for key, icon, name, meta in LAYERS:
        cls, value = states[key]
        cards.append(f"<div class='layer {cls}'><span>{key}</span><b>{icon} {esc(name)}</b><strong>{esc(value)}</strong><small>{esc(meta)}</small></div>")
    mode = "KONTROLOWANY" if RUN["mode"] == "controlled" else "NAIWNY"
    return f"<div class='mode {RUN['mode']}'>{mode} · {esc(PROFILES.get(RUN['profile'], RUN['profile']))}</div><div class='layers'>{''.join(cards)}</div>"


def panel(title: str, subtitle: str, body: str) -> str:
    return f"<section class='panel'><h2>{esc(title)}</h2><p>{esc(subtitle)}</p>{body}</section>"


def render_kpi() -> str:
    if not RUN["ran"]:
        vals = [("Zdolności", "—"), ("Wysłane maile", "—"), ("Redukcja", "—"), ("Evidence", "—"), ("Blokady", "—")]
    elif RUN["mode"] == "naive":
        vals = [("Zdolności", "∞"), ("Wysłane maile", "1"), ("Redukcja", "0%"), ("Evidence", "0"), ("Blokady", "0")]
    else:
        b = RUN["budget"]
        vals = [("Zdolności", "3"), ("Wysłane maile", "0"), ("Redukcja", f"{b.get('reduction_pct', 0)}%"),
                ("Evidence", str(len(RUN["evidence"]))), ("Blokady", str(sum(r['state'] == 'blocked' for r in RUN['tool_log'])))]
    return "<div class='kpis'>" + "".join(f"<div><small>{label}</small><b>{value}</b></div>" for label, value in vals) + "</div>"


def render_budget() -> str:
    if not RUN["ran"]:
        return panel("💸 Budżet kontekstu", "Warstwa 5 w liczbach.", "<div class='empty'>Uruchom poranek.</div>")
    b = RUN["budget"]
    raw, clean = b.get("raw_chars", 0), b.get("context_chars", 0)
    width = min(100, max(2, round(100 * clean / max(raw, 1))))
    body = f"<div class='metrics'><div>surowy wynik<b>{raw:,} znaków</b></div><div>w kontekście<b>{clean:,} znaków</b></div><div>reduction<b>{b.get('reduction_pct', 0)}%</b></div></div><div class='bar'><i style='width:{width}%'></i></div>"
    return panel("💸 Budżet kontekstu", "Nie oszczędność. Warunek trafności.", body)


def render_briefing() -> str:
    if not RUN["ran"]:
        return panel("📋 Poranny Briefing", "Produkt.", "<div class='empty'>Kliknij Uruchom poranek.</div>")
    if RUN["mode"] == "naive":
        return panel("📋 Poranny Briefing", "Produkt.", "<div class='alarm'>Naiwny pipeline nie tworzy briefingu z evidence. Tworzy odpowiedź, której nie da się sprawdzić.</div>")
    briefing = RUN["briefing"]
    if not briefing.priorities:
        body = "<div class='empty'>Dziś nie ma podstaw do tej akcji. Robot nie wymyśla pracy.</div>"
    else:
        body = "".join(f"<div class='priority'><b>{p.rank:02d}</b><div><strong>{esc(p.title)}</strong><p>dlaczego teraz: {esc(p.why_now)}</p><em>{' · '.join(map(esc, p.evidence_ids))}</em></div></div>" for p in briefing.priorities)
    drafts = "".join(f"<div class='draft'><b>{esc(d.subject)}</b><small>→ {esc(d.to)} · {' '.join(d.evidence_ids)}</small></div>" for d in briefing.drafts)
    return panel("📋 Poranny Briefing", f"{len(briefing.priorities)} priorytety · {len(briefing.drafts)} drafty", body + "<h3>Przygotowane drafty</h3>" + (drafts or "<div class='empty'>Brak draftów.</div>"))


def render_evidence() -> str:
    if not RUN["ran"]:
        return panel("🔬 Evidence", "Skąd robot to wie?", "<div class='empty'>Uruchom poranek.</div>")
    if RUN["mode"] == "naive":
        return panel("🔬 Evidence", "Skąd robot to wie?", "<div class='alarm'>Brak. System nie umie odpowiedzieć na pytanie: skąd to wiesz?</div>")
    rows = "".join(f"<div class='evidence {'flagged' if item.suspicious else ''}'><b>{esc(item.id)}</b><small>{esc(item.source)} · {esc(item.who)}</small><p>{esc(item.summary)}</p><em>ref: {esc(item.ref)}</em>{'<i>OFLAGOWANE</i>' if item.suspicious else ''}</div>" for item in RUN['evidence'])
    return panel("🔬 Evidence", "Oflagowane źródło jest widoczne, ale nie ma władzy.", rows or "<div class='empty'>Zero dowodów.</div>")


def render_gate() -> str:
    if not RUN["ran"]:
        return panel("🧑‍⚖️ Human Gate", "Warstwa 6.", "<div class='empty'>Uruchom poranek.</div>")
    gate = RUN["gate"]
    if gate["state"] == "sent":
        return panel("🧑‍⚖️ Human Gate", "Warstwa 6 - wyłączona.", "<div class='alarm'>📤 MAIL JUŻ WYSŁANY. Nikt nie pytał.</div>")
    payload = gate["payload"]
    body = f"<div class='gate {esc(gate['state'])}'>{esc(gate['state'].upper())}</div><p>{esc(gate['reason'])}</p><pre>to: {esc(payload.get('to', '—'))}\nsubject: {esc(payload.get('subject', '—'))}\n\n{esc(payload.get('body', ''))}</pre><div class='note'>Nie ma przycisku Wyślij. Nie jest ukryty - nie istnieje w kodzie.</div>"
    return panel("🧑‍⚖️ Human Gate", "Robot przygotował. Ostatnia sekunda należy do Ciebie.", body)


def render_audit() -> str:
    if not RUN["audit"]:
        return panel("🧾 Audit Trail", "Warstwa 7.", "<div class='empty'>Brak wpisów.</div>")
    rows = "".join(f"<div class='audit {'human' if x['actor'] == 'human' else ''}'><small>{x['at']} · L{x['layer']} · {esc(x['actor'])}</small><b>{esc(x['event'])}</b><p>{esc(x['detail'])}</p></div>" for x in RUN['audit'])
    return panel("🧾 Audit Trail", "Co widział, co zablokował, kto zdecydował.", rows)


def render_log() -> str:
    rows = "".join(f"<div class='log {esc(x['state'])}'><b>{esc(x['name'])}</b><span>{esc(x['state'])}</span><p>{esc(x['reason'])}</p></div>" for x in RUN['tool_log'])
    return panel("⚙️ Log wywołań", "Każde wywołanie ma powód.", rows or "<div class='empty'>Brak wywołań.</div>")


def all_views():
    policy_json = RUN["policy"].model_dump_json(indent=2) if RUN["policy"] else "{}"
    return (layer_cards(), render_kpi(), render_budget(), render_briefing(), render_gate(), render_audit(),
            render_evidence(), render_log(), policy_json, RUN["raw"], RUN["clean"], RUN["status"])


def run(profile: str, mode: str, max_results: int, max_body: int, autonomy: str):
    if mode.startswith("NAIWNY"):
        naive(profile)
    else:
        controlled(profile, max_results, max_body, autonomy)
    return all_views()


def approve():
    if RUN["gate"]["state"] not in {"waiting", "auto"}:
        RUN["status"] = "⚠️ Najpierw uruchom kontrolowany poranek."
    else:
        RUN["gate"]["state"] = "approved"
        audit(6, "human_approved", "zatwierdzone bez zmian", actor="human")
        audit(7, "simulated_side_effect", "Google nietknięte")
        RUN["status"] = "✅ Zatwierdzone w symulacji. Decyzja człowieka jest w audycie."
    return all_views()


def skip():
    if RUN["ran"]:
        RUN["gate"]["state"] = "skipped"
        audit(6, "human_skipped", "człowiek odrzucił propozycję", actor="human")
        RUN["status"] = "⏭️ Odrzucone. Odrzucenie też jest danymi."
    return all_views()


def edit_and_approve(body: str):
    if RUN["gate"]["state"] not in {"waiting", "auto", "approved"}:
        RUN["status"] = "⚠️ Najpierw uruchom kontrolowany poranek."
        return all_views()
    before = len(RUN["gate"]["payload"].get("body", ""))
    RUN["gate"]["payload"]["body"] = body.strip()
    RUN["gate"]["state"] = "edited"
    audit(6, "human_edited", f"treść zmieniona: {before} → {len(body.strip())} znaków", actor="human")
    audit(7, "promotion_signal", "edycja oznacza: ta klasa nie awansuje na auto")
    RUN["status"] = "✏️ Poprawione w symulacji. Edycja została zapisana jako sygnał dla polityki autonomii."
    return all_views()


def export_audit() -> str:
    payload = {
        "generated_at": datetime.now().isoformat(), "profile": RUN["profile"], "mode": RUN["mode"],
        "policy": json.loads(RUN["policy"].model_dump_json()) if RUN["policy"] else None,
        "context_budget": RUN["budget"], "tool_log": RUN["tool_log"], "audit": RUN["audit"],
        "external_side_effects": "none (workshop simulation)",
    }
    with tempfile.NamedTemporaryFile("w", suffix="_briefing_audit.json", delete=False, encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        return file.name


def test(kind: str):
    if not RUN["ran"] or RUN["mode"] != "controlled":
        RUN["status"] = "⚠️ Najpierw uruchom tryb KONTROLOWANY."
        return all_views()
    if kind == "id":
        call = h.ToolCall(tool=h.ToolName.GET_THREAD, arguments={"thread_id": "invented_id"})
        allowed, reason = h.is_allowed(call, RUN["policy"], {x.ref for x in RUN["evidence"]})
        log_tool("get_thread", "ok" if allowed else "blocked", reason)
        audit(3, "test_invented_id", reason)
        RUN["status"] = "🎭 Poprawny string bez ugruntowania nie ma prawa istnieć."
    elif kind == "tool":
        try:
            h.ToolName("send_email")
            RUN["status"] = "⚠️ send_email istnieje w katalogu - to błąd projektu."
        except ValueError:
            log_tool("send_email", "impossible", "ValueError: brak reprezentacji")
            audit(1, "test_forbidden_tool", "send_email nie istnieje w enumie")
            RUN["status"] = "🚫 send_email nie da się nawet zbudować jako obiektu."
    elif kind == "limit":
        log_tool("search_gmail", "clamped", f"żądanie 50 → limit z policy: {RUN['policy'].max_results}")
        audit(3, "test_limit_override", "router obciął limit modelu")
        RUN["status"] = "📚 System zna trzy stany: przepuść, obetnij, odmów."
    else:
        log_tool("get_thread", "failed", "TimeoutError: gog nie odpowiedział")
        audit(4, "tool_failure", "awaria jest stanem, nie pustym stringiem")
        RUN["status"] = "💥 Awaria narzędzia została zapisana, bez udawania sukcesu."
    return all_views()


CSS = """
:root{--bg:#070b12;--p:#101827;--line:#233047;--tx:#ecf5ff;--mut:#9aabc0;--mint:#70f7ba;--cyan:#49d7ff;--red:#fb7185;--amber:#ffc45c}
body,.gradio-container{background:radial-gradient(circle at 10% 0,#103b363d,transparent 30%),radial-gradient(circle at 95% 0,#34205b48,transparent 28%),var(--bg)!important;color:var(--tx)!important}.gradio-container{max-width:1540px!important;padding:18px!important}.hero{padding:28px;border:1px solid var(--line);border-radius:26px;background:linear-gradient(135deg,#101b2a,#0b0e15)}.hero h1{font-size:36px;margin:6px 0}.eyebrow{color:var(--mint);letter-spacing:.15em;font-size:11px}.mode{margin:14px 0 8px;color:var(--mut);font-size:12px}.mode.controlled{color:var(--mint)}.mode.naive{color:var(--red)}.layers{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}.layer,.panel,.kpis>div{background:linear-gradient(180deg,#111b2a,#0c121d);border:1px solid var(--line);border-radius:16px}.layer{min-height:94px;padding:12px;display:flex;flex-direction:column;gap:5px}.layer span{font-size:10px;color:var(--mut)}.layer b{font-size:12px}.layer strong{color:var(--mint);font-size:15px}.layer small{color:var(--mut);font-size:10px}.layer.dead{border-style:dashed;border-color:var(--red)}.layer.dead strong{color:var(--red)}.layer.warn,.layer.wait{border-color:var(--amber)}.layer.warn strong,.layer.wait strong{color:var(--amber)}.layer.flag,.layer.block{border-color:var(--cyan)}.layer.flag strong,.layer.block strong{color:var(--cyan)}.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:12px 0}.kpis>div{padding:12px}.kpis small{display:block;color:var(--mut);font-size:10px;text-transform:uppercase}.kpis b{font-size:25px}.panel{padding:16px;margin-bottom:12px}.panel h2{font-size:17px;margin:0}.panel>p{margin:3px 0 12px;color:var(--mut);font-size:12px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.metrics div{color:var(--mut);font-size:11px}.metrics b{display:block;color:var(--tx);font-size:16px;margin-top:4px}.bar{height:10px;background:#1d293b;border-radius:99px;margin-top:12px}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--mint),var(--cyan));border-radius:99px}.priority{display:grid;grid-template-columns:42px 1fr;gap:8px;padding:11px;margin:7px 0;border-radius:12px;background:#ffffff08}.priority>b{color:var(--mint);font-size:18px}.priority p,.priority em{font-size:12px;color:var(--mut);margin:4px 0}.draft,.evidence,.audit,.log{padding:10px;margin:7px 0;border-radius:11px;background:#ffffff08;border:1px solid #ffffff0a}.draft small,.evidence small,.audit small{display:block;color:var(--mut);margin-top:3px}.evidence.flagged{border-color:var(--red);background:#fb718512}.evidence i{float:right;color:var(--red);font-size:10px}.evidence p,.audit p,.log p{margin:5px 0;font-size:12px}.gate{display:inline-block;padding:6px 10px;border-radius:99px;background:#ffc45c22;color:var(--amber);font-size:12px;font-weight:700}.gate.approved,.gate.auto{background:#70f7ba1e;color:var(--mint)}pre{white-space:pre-wrap;word-break:break-word;background:#0004;padding:10px;border-radius:10px;color:#d9e8fa;font-size:12px}.note,.empty{padding:10px;color:var(--mut);background:#ffffff05;border-radius:10px}.alarm{padding:12px;color:#ffe0e5;background:#fb71851a;border:1px solid #fb718560;border-radius:10px}.log span{color:var(--cyan);font-size:11px;margin-left:8px}.log.blocked,.log.impossible{border-color:var(--red)}@media(max-width:1200px){.layers{grid-template-columns:repeat(4,1fr)}.kpis{grid-template-columns:repeat(3,1fr)}}
"""


def build_app() -> gr.Blocks:
    with gr.Blocks(title="DWthon Tools - Poranny Briefing", css=CSS) as app:
        gr.HTML("<div class='hero'><div class='eyebrow'>DWTHON TOOLS · DAY 3 · PORANNY BRIEFING</div><h1>Robot przygotowuje. Ty decydujesz.</h1><p>Trzy zdolności read-only, policy z powodem odmowy, evidence, granica człowieka i pełny ślad audytowy.</p></div>")
        with gr.Row():
            profile = gr.Dropdown(list(PROFILES), value="founder", label="Morning scenario", scale=2)
            mode = gr.Radio(["KONTROLOWANY (7 warstw)", "NAIWNY (bez granic)"], value="KONTROLOWANY (7 warstw)", label="Tryb", scale=3)
            run_button = gr.Button("▶️ Uruchom poranek", variant="primary", scale=2)
        with gr.Row():
            max_results = gr.Slider(1, 10, value=4, step=1, label="policy.max_results")
            max_body = gr.Slider(60, 1200, value=240, step=20, label="policy.max_body_chars")
            autonomy = gr.Radio([h.AutonomyMode.APPROVAL_REQUIRED.value, h.AutonomyMode.AUTO_ELIGIBLE.value], value=h.AutonomyMode.APPROVAL_REQUIRED.value, label="policy.autonomy_mode")
        pipeline = gr.HTML(layer_cards()); kpis = gr.HTML(render_kpi())
        with gr.Row():
            with gr.Column(scale=7):
                budget = gr.HTML(render_budget()); briefing = gr.HTML(render_briefing()); gate = gr.HTML(render_gate())
                with gr.Row():
                    approve_button = gr.Button("✅ Zatwierdź (symulacja)", variant="primary")
                    edit_button = gr.Button("✏️ Popraw i zatwierdź")
                    skip_button = gr.Button("⏭️ Pomiń")
                edit_body = gr.Textbox(label="Treść do poprawy - edycja blokuje awans klasy na auto", lines=4)
                audit_panel = gr.HTML(render_audit())
            with gr.Column(scale=5):
                status = gr.HTML("<div class='note'>Kliknij Uruchom poranek.</div>")
                evidence = gr.HTML(render_evidence())
                gr.Markdown("### 🧨 Testy odporności")
                with gr.Row():
                    invented = gr.Button("🎭 Wymyślone ID"); forbidden = gr.Button("📤 send_email")
                with gr.Row():
                    limit = gr.Button("📚 50 wyników"); failure = gr.Button("💥 Awaria narzędzia")
                log = gr.HTML(render_log())
        with gr.Accordion("🔧 Pod maską", open=False):
            policy_json = gr.Code("{}", language="json", label="Policy")
            raw = gr.Textbox("", lines=10, label="Raw response", interactive=False)
            clean = gr.Textbox("", lines=10, label="Evidence po normalizacji", interactive=False)
            export_button = gr.Button("📥 Pobierz Audit JSON", size="sm")
            export_file = gr.File(label="Audit JSON")
        outputs = [pipeline, kpis, budget, briefing, gate, audit_panel, evidence, log, policy_json, raw, clean, status]
        run_button.click(run, [profile, mode, max_results, max_body, autonomy], outputs)
        approve_button.click(approve, None, outputs); skip_button.click(skip, None, outputs)
        edit_button.click(edit_and_approve, [edit_body], outputs)
        invented.click(lambda: test("id"), None, outputs); forbidden.click(lambda: test("tool"), None, outputs)
        limit.click(lambda: test("limit"), None, outputs); failure.click(lambda: test("failure"), None, outputs)
        export_button.click(export_audit, None, [export_file])
    return app


if __name__ == "__main__":
    print("DWthon Tools - Poranny Briefing")
    print("Wysyłka: NIE ISTNIEJE w kodzie. Zatwierdzenie zapisuje tylko audit.")
    build_app().queue().launch(server_name="0.0.0.0", server_port=PORT, root_path=ROOT_PATH or None)
