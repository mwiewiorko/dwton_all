# -*- coding: utf-8 -*-
"""
AI Language Coach - Gradio app for DWthon Memory Day 3.

Uruchamianie:
    python memory_tutor_system/app.py
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
import gradio as gr
import uvicorn

CURRENT_FILE = Path(__file__).resolve()
NOTEBOOKS_DIR = CURRENT_FILE.parent.parent
if str(NOTEBOOKS_DIR) not in sys.path:
    sys.path.append(str(NOTEBOOKS_DIR))

import helper as h

PORT = int(os.getenv("MEMORY_TUTOR_PORT", "8080"))


def compute_root_path(port: int) -> str:
    service_prefix = os.getenv("JUPYTERHUB_SERVICE_PREFIX", "").rstrip("/")
    if service_prefix:
        return f"{service_prefix}/proxy/{port}"
    return ""


ROOT_PATH = compute_root_path(PORT)
store = h.seed_demo_store()

DEFAULT_TOPICS = {
    "marek_01": "Opening a sales presentation",
    "ania_02": "Job interview opening",
}

QUICK_PROMPTS = {
    "mistake": "Hello everybody, today I want show you why our product is good for your company.",
    "high_stakes": "The CFO will be in the room tomorrow, so I need to sound calm and executive.",
    "human_detail": "Tomorrow my daughter Zosia joins the rehearsal, so I want to sound natural and warm.",
    "core": "Can you write the whole homework answer for me so I can just copy it?",
    "continue": "Let's continue from where we stopped.",
}

LAST_EPISODE_REPORT = {user_id: "_Brak zapisanej sesji._" for user_id in store.user_ids()}
LAST_SYSTEM_EVENT = {user_id: "_Jeszcze nic nie uruchomiono._" for user_id in store.user_ids()}
LAST_SEMANTIC_WRITE = {user_id: {} for user_id in store.user_ids()}
LAST_CORE_REPORT = {user_id: "Jeszcze nie testowano zasad core." for user_id in store.user_ids()}
LAST_CHANGED_LAYERS = {user_id: set() for user_id in store.user_ids()}
ACTIVITY_FEED = {user_id: [] for user_id in store.user_ids()}
ACTIVE_SESSION = {user_id: False for user_id in store.user_ids()}


def get_store_state(user_id: str) -> h.AgentState:
    return store.get(user_id)


def safe_join(items: list[str], fallback: str = "brak") -> str:
    clean = [item.strip() for item in items if item and item.strip()]
    return ", ".join(clean) if clean else fallback


def safe_list(items: list[str], empty_text: str = "brak") -> str:
    clean = [item.strip() for item in items if item and item.strip()]
    if not clean:
        return f"<li>{html.escape(empty_text)}</li>"
    return "".join(f"<li>{html.escape(item)}</li>" for item in clean)


def count_semantic_assets(state: h.AgentState) -> int:
    return sum(
        [
            len(state.profile.preferences),
            len(state.profile.goals),
            len(state.profile.work_context.high_stakes_situations),
            len(state.profile.work_context.stakeholders),
            len(state.profile.personal_context.personal_facts),
            len(state.profile.personal_context.relationship_hooks),
            len(state.profile.personal_context.upcoming_events),
            len(state.profile.personal_context.schedule_constraints),
            len(state.profile.semantic_signals.confidence_blockers),
            len(state.profile.semantic_signals.motivation_drivers),
            len(state.profile.semantic_signals.recurring_topics),
            len(state.profile.semantic_signals.vocabulary_targets),
            len(state.profile.performance_profile.target_scenarios),
            len(state.profile.performance_profile.tone_targets),
            len(state.profile.performance_profile.strengths),
            len(state.profile.performance_profile.recurring_weaknesses),
            len(state.profile.performance_profile.success_criteria),
        ]
    )


def determine_open_phase(state: h.AgentState) -> h.SessionPhase:
    if state.progress.homework_status == "assigned" or state.progress.recent_mistakes:
        return h.SessionPhase.HOMEWORK_CHECK
    return h.SessionPhase.PRACTICE


def maybe_advance_phase(state: h.AgentState) -> str | None:
    user_turns = sum(1 for item in state.working.history if item["role"] == "user")
    if state.workflow.phase == h.SessionPhase.HOMEWORK_CHECK and user_turns >= 1:
        state.workflow.phase = h.SessionPhase.PRACTICE
        return "KK automatycznie przestawiły sesję z `HOMEWORK_CHECK` na `PRACTICE` po krótkim check-inie."
    if state.workflow.phase == h.SessionPhase.PRACTICE and user_turns >= 4:
        state.workflow.phase = h.SessionPhase.WRAP_UP
        return "KK automatycznie przestawiły sesję z `PRACTICE` na `WRAP_UP`, bo mamy już dość materiału na domknięcie."
    return None


def snapshot_state(state: h.AgentState) -> dict:
    return {
        "phase": state.workflow.phase.value,
        "topic": state.workflow.topic,
        "working_count": len(state.working.history),
        "semantic_assets": count_semantic_assets(state),
        "context_notes": state.profile.context_notes or "",
        "mistakes": tuple(state.progress.recent_mistakes),
        "next_focus": state.progress.next_focus or "",
        "homework_status": state.progress.homework_status,
        "episodes": len(state.episodic.episodes),
    }


def compare_snapshots(before: dict, after: dict, core_reason: str | None = None) -> tuple[set[str], str]:
    changed: set[str] = set()
    lines: list[str] = []

    if before["working_count"] != after["working_count"]:
        changed.add("working")
        lines.append(
            f"- `Working` → {before['working_count']} → {after['working_count']} elementów w aktywnym oknie rozmowy."
        )

    if before["semantic_assets"] != after["semantic_assets"] or before["context_notes"] != after["context_notes"]:
        changed.add("semantic")
        lines.append(
            f"- `Semantic` → assety: {before['semantic_assets']} → {after['semantic_assets']} | "
            f"summary: `{after['context_notes'] or 'brak'}`"
        )

    if (
        before["mistakes"] != after["mistakes"]
        or before["next_focus"] != after["next_focus"]
        or before["homework_status"] != after["homework_status"]
    ):
        changed.add("progress")
        changed.add("semantic")
        lines.append(
            f"- `Progress` → next focus: `{after['next_focus'] or 'brak'}` | homework: `{after['homework_status']}`"
        )

    if before["episodes"] != after["episodes"]:
        changed.add("episodic")
        lines.append(f"- `Episodic` → epizody: {before['episodes']} → {after['episodes']}.")

    if before["phase"] != after["phase"] or before["topic"] != after["topic"]:
        changed.add("workflow")
        lines.append(f"- `Workflow` → faza: `{before['phase']}` → `{after['phase']}` | temat: `{after['topic']}`.")

    if core_reason:
        changed.add("core")
        lines.append(f"- `Core` → guardrail aktywny: {core_reason}")

    if not lines:
        lines.append("- Brak trwałej mutacji stanu po ostatniej akcji.")

    return changed, "### Ostatnia mutacja stanu\n\n" + "\n".join(lines)


def format_episode_report(episode: h.Episode) -> str:
    mistakes = "\n".join(
        f"- `{m.user_phrase}` → `{m.correction}` ({m.rule})"
        for m in episode.mistakes
    ) or "- brak"
    facts = "\n".join(f"- {fact}" for fact in episode.personal_facts) or "- brak"

    return f"""### 🧾 Session report

**Summary**
- {episode.summary}

**Mistakes**
{mistakes}

**Next step**
- {episode.next_step or 'brak'}

**Personal facts**
{facts}
"""


def render_store_overview_html() -> str:
    cards = []
    for user_id in store.user_ids():
        state = store.get(user_id)
        cards.append(
            f"""
            <div class='store-card'>
              <div class='store-head'>{html.escape(state.profile.name)} <span>{html.escape(user_id)}</span></div>
              <div class='store-line'>epizody: <b>{len(state.episodic.episodes)}</b></div>
              <div class='store-line'>next focus: <b>{html.escape(state.progress.next_focus or 'brak')}</b></div>
              <div class='store-line'>semantic assety: <b>{count_semantic_assets(state)}</b></div>
            </div>
            """
        )
    return "<div class='store-grid'>" + "".join(cards) + "</div>"


def wrap_status(text: str) -> str:
    return f"<div class='status-shell'>{html.escape(text)}</div>"


def build_event_html(user_id: str) -> str:
    raw = LAST_SYSTEM_EVENT[user_id] or ""
    title = "Co się właśnie zmieniło"
    items: list[str] = []

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("###"):
            title = stripped.replace("###", "", 1).strip()
        elif stripped.startswith("-"):
            items.append(stripped[1:].strip().replace("`", ""))
        else:
            items.append(stripped.replace("`", ""))

    if not items:
        body = "<div class='empty-note slim'>Jeszcze nic nie uruchomiono.</div>"
    else:
        body = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        body = f"<ul class='mini-list event-list'>{body}</ul>"

    return f"""
    <div class='memory-panel event-panel'>
      <div class='panel-title'>🔴 {html.escape(title)}</div>
      <div class='panel-sub'>To jest delta pamięci i procesu po ostatniej akcji użytkownika.</div>
      {body}
    </div>
    """


def push_activity(user_id: str, icon: str, text: str):
    stamp = datetime.now().strftime("%H:%M:%S")
    ACTIVITY_FEED[user_id].insert(0, {"time": stamp, "icon": icon, "text": text})
    ACTIVITY_FEED[user_id] = ACTIVITY_FEED[user_id][:8]


def build_activity_html(user_id: str) -> str:
    rows = ACTIVITY_FEED[user_id]
    if not rows:
        body = "<div class='empty-note slim'>Brak aktywności. Otwórz sesję, a system zacznie zostawiać ślady.</div>"
    else:
        body = "".join(
            f"""
            <div class='activity-item'>
              <div class='activity-icon'>{html.escape(item['icon'])}</div>
              <div class='activity-body'>
                <div class='activity-time'>{html.escape(item['time'])}</div>
                <div class='activity-text'>{html.escape(item['text'])}</div>
              </div>
            </div>
            """
            for item in rows
        )
    return f"""
    <div class='memory-panel activity-panel'>
      <div class='panel-title'>💓 Heartbeat systemu</div>
      <div class='panel-sub'>Tu widać, że to nie jest statyczny chat. System żyje, reaguje i zostawia ślady w czasie.</div>
      <div class='activity-stack'>{body}</div>
    </div>
    """


def assistant_opener(state: h.AgentState) -> str:
    name = state.profile.name
    if state.workflow.phase == h.SessionPhase.HOMEWORK_CHECK and state.progress.next_focus:
        return (
            f"Cześć {name}! Wracamy do ostatniego kroku: {state.progress.next_focus}. "
            f"Zacznijmy od krótkiej próbki, a potem płynnie przejdziemy dalej."
        )

    hook = state.profile.personal_context.relationship_hooks[-1] if state.profile.personal_context.relationship_hooks else ""
    high_stakes = (
        state.profile.work_context.high_stakes_situations[0]
        if state.profile.work_context.high_stakes_situations
        else state.workflow.topic
    )
    if hook:
        return f"Cześć {name}! {hook} Dziś skupimy się na: {high_stakes}. Zacznij po angielsku, a ja Cię poprowadzę."
    return f"Cześć {name}! Dziś skupimy się na: {high_stakes}. Zacznij po angielsku, a ja Cię poprowadzę."


def semantic_write_to_dict(candidate: h.SemanticWriteCandidate) -> dict:
    raw = candidate.model_dump()
    return {key: value for key, value in raw.items() if value}


def render_persona_html(state: h.AgentState) -> str:
    primary_goal = state.profile.goals[0] if state.profile.goals else None
    deadline = primary_goal.deadline if primary_goal else None
    success_metric = primary_goal.success_metric if primary_goal else None
    return f"""
    <div class='persona-card'>
      <div class='persona-head'>
        <div>
          <div class='eyebrow'>AKTYWNY UCZEŃ</div>
          <h2>{html.escape(state.profile.name)}</h2>
        </div>
        <div class='level-pill'>{html.escape(state.profile.level)}</div>
      </div>
      <p class='persona-goal'>{html.escape(state.profile.goal)}</p>
      <div class='persona-grid'>
        <div class='persona-cell'>
          <span>Rola</span>
          <b>{html.escape(state.profile.work_context.profession or 'brak')}</b>
        </div>
        <div class='persona-cell'>
          <span>Firma</span>
          <b>{html.escape(state.profile.work_context.company_name or 'brak')}</b>
        </div>
        <div class='persona-cell'>
          <span>Deadline</span>
          <b>{html.escape(deadline or 'brak')}</b>
        </div>
        <div class='persona-cell'>
          <span>Sukces</span>
          <b>{html.escape(success_metric or 'brak')}</b>
        </div>
      </div>
    </div>
    """


def build_guidance_html(state: h.AgentState, user_id: str) -> str:
    step = 2
    if not ACTIVE_SESSION[user_id] and len(state.episodic.episodes) == 0:
        step = 2
        headline = "Najpierw otwórz sesję. Potem kliknij szybki scenariusz i zobacz, jak pamięć reaguje na żywo."
    elif ACTIVE_SESSION[user_id] and sum(1 for m in state.working.history if m["role"] == "user") == 0:
        step = 3
        headline = "Sesja jest otwarta. Wybierz teraz scenariusz: błąd, wysoka stawka, ludzki detal albo test Core."
    elif ACTIVE_SESSION[user_id]:
        step = 5
        headline = "Patrz teraz na Semantic, Working i Workflow. To jest moment, w którym system pokazuje swoją przewagę."
    elif len(state.episodic.episodes) > 0:
        step = 7
        headline = "Masz zapisany epizod. Otwórz nową sesję i zobacz, że coach nie wraca do zera."
    else:
        step = 1
        headline = "Załaduj demo-personę i przejdź przez prosty flow produktu."

    labels = [
        "Załaduj personę",
        "Otwórz sesję",
        "Kliknij scenariusz",
        "Wyślij wiadomość",
        "Obserwuj warstwy",
        "Zamknij i zapisz",
        "Otwórz nową sesję",
    ]
    step_html = []
    for idx, label in enumerate(labels, start=1):
        cls = "step-pill"
        if idx < step:
            cls += " done"
        elif idx == step:
            cls += " current"
        step_html.append(f"<div class='{cls}'><span>{idx}</span>{html.escape(label)}</div>")

    return f"""
    <div class='guide-card'>
      <div class='guide-eyebrow'>CO ZROBIĆ TERAZ</div>
      <div class='guide-headline'>{html.escape(headline)}</div>
      <div class='step-grid'>{''.join(step_html)}</div>
    </div>
    """


def build_kpi_html(state: h.AgentState) -> str:
    return f"""
    <div class='kpi-grid'>
      <div class='kpi-card'>
        <div class='kpi-label'>Semantic assety</div>
        <div class='kpi-value'>{count_semantic_assets(state)}</div>
      </div>
      <div class='kpi-card'>
        <div class='kpi-label'>Working window</div>
        <div class='kpi-value'>{len(state.working.history)}</div>
      </div>
      <div class='kpi-card'>
        <div class='kpi-label'>Epizody</div>
        <div class='kpi-value'>{len(state.episodic.episodes)}</div>
      </div>
      <div class='kpi-card'>
        <div class='kpi-label'>Faza</div>
        <div class='kpi-value small'>{html.escape(state.workflow.phase.value)}</div>
      </div>
    </div>
    """


def build_layer_pulse_html(state: h.AgentState, user_id: str) -> str:
    changed = LAST_CHANGED_LAYERS[user_id]
    cards = [
        ("core", "🏛️ Core", "zasady i granice", "guardrails"),
        ("semantic", "🧠 Semantic", f"{count_semantic_assets(state)} assetów", "profil + postęp"),
        ("workflow", "🎯 Workflow", state.workflow.phase.value, "KK sterują zakresem"),
        ("working", "⚡ Working", f"{len(state.working.history)} msg", "to widzi model teraz"),
        ("episodic", "📚 Episodic", f"{len(state.episodic.episodes)} sesji", "skondensowana historia"),
        ("procedural", "🛠️ Procedural", "poza MVP", "pełna ścieżka"),
        ("resource", "🔧 Resource", "poza MVP", "pełna ścieżka"),
    ]
    blocks = []
    for key, title, value, meta in cards:
        classes = ["layer-card"]
        if key in changed:
            classes.append("changed")
        if key in {"procedural", "resource"}:
            classes.append("dim")
        blocks.append(
            f"""
            <div class='{' '.join(classes)}'>
              <div class='layer-title'>{title}</div>
              <div class='layer-value'>{html.escape(str(value))}</div>
              <div class='layer-meta'>{html.escape(meta)}</div>
            </div>
            """
        )
    return "<div class='layer-grid'>" + "".join(blocks) + "</div>"


def build_working_html(state: h.AgentState) -> str:
    rows = []
    for item in state.working.get_context_window():
        role = "Uczeń" if item["role"] == "user" else "Coach"
        role_cls = "user" if item["role"] == "user" else "assistant"
        rows.append(
            f"<div class='memory-row {role_cls}'><span>{role}</span><p>{html.escape(item['content'])}</p></div>"
        )
    body = "".join(rows) if rows else "<div class='empty-note'>Brak wiadomości. Ta warstwa ożywa od pierwszej tury.</div>"
    return f"""
    <div class='memory-panel'>
      <div class='panel-title'>⚡ Working Memory</div>
      <div class='panel-sub'>To jest aktywne okno rozmowy. Dokładnie ten fragment trafia teraz do modelu.</div>
      <div class='memory-stack'>{body}</div>
    </div>
    """


def build_semantic_html(state: h.AgentState, user_id: str) -> str:
    write = LAST_SEMANTIC_WRITE[user_id]
    write_blocks = []
    for key, value in write.items():
        label = key.replace("_", " ")
        if isinstance(value, list):
            body = ", ".join(value)
        else:
            body = str(value)
        write_blocks.append(
            f"<div class='write-chip new'><span>{html.escape(label)}</span>{html.escape(body)}</div>"
        )
    last_write = "".join(write_blocks) or "<div class='empty-note slim'>Brak nowego zapisu z ostatniej tury.</div>"

    goals = "".join(
        f"<li>{html.escape(goal.goal)}<br><small>{html.escape(goal.success_metric or 'brak miernika')}</small></li>"
        for goal in state.profile.goals
    ) or "<li>brak</li>"

    human_context = state.profile.personal_context.personal_facts + state.profile.personal_context.upcoming_events
    vocab_and_tone = (
        state.profile.semantic_signals.vocabulary_targets + state.profile.performance_profile.tone_targets
    )

    return f"""
    <div class='memory-panel'>
      <div class='panel-title'>🧠 Semantic Memory</div>
      <div class='panel-sub'>To nie jest jedna notatka. To profil operacyjny, który zwiększa jakość każdej kolejnej sesji.</div>
      <div class='semantic-grid'>
        <div class='semantic-card'>
          <div class='mini-title'>Mission</div>
          <ul class='mini-list'>{goals}</ul>
        </div>
        <div class='semantic-card'>
          <div class='mini-title'>Work context</div>
          <ul class='mini-list'>
            <li>{html.escape(state.profile.work_context.profession or 'brak')} @ {html.escape(state.profile.work_context.company_name or 'brak')}</li>
            <li>{html.escape(state.profile.work_context.industry or 'brak')}</li>
            <li>{html.escape(safe_join(state.profile.work_context.high_stakes_situations))}</li>
          </ul>
        </div>
        <div class='semantic-card'>
          <div class='mini-title'>Human context</div>
          <ul class='mini-list'>{safe_list(human_context)}</ul>
        </div>
        <div class='semantic-card'>
          <div class='mini-title'>Coach settings</div>
          <ul class='mini-list'>
            <li>feedback: {html.escape(state.profile.communication_preferences.feedback_style or 'brak')}</li>
            <li>intensity: {html.escape(state.profile.communication_preferences.correction_intensity or 'brak')}</li>
            <li>tempo: {html.escape(state.profile.communication_preferences.pace or 'brak')}</li>
          </ul>
        </div>
        <div class='semantic-card'>
          <div class='mini-title'>Confidence map</div>
          <ul class='mini-list'>{safe_list(state.profile.semantic_signals.confidence_blockers)}</ul>
        </div>
        <div class='semantic-card'>
          <div class='mini-title'>Vocabulary + tone</div>
          <ul class='mini-list'>{safe_list(vocab_and_tone)}</ul>
        </div>
      </div>
      <div class='mini-title live-title'>Ostatni zapis semantyczny z rozmowy</div>
      <div class='write-grid'>{last_write}</div>
    </div>
    """


def build_progress_html(state: h.AgentState) -> str:
    mistakes = "".join(f"<li>{html.escape(item)}</li>" for item in state.progress.recent_mistakes) or "<li>brak</li>"
    return f"""
    <div class='memory-panel'>
      <div class='panel-title'>📈 Progress (część Semantic)</div>
      <div class='panel-sub'>Ta warstwa nie trzyma "kim jesteś", tylko "jak Ci idzie" i co ma się wydarzyć dalej.</div>
      <div class='split-grid'>
        <div>
          <div class='mini-title'>Ostatnie błędy</div>
          <ul class='mini-list'>{mistakes}</ul>
        </div>
        <div>
          <div class='mini-title'>Następny krok</div>
          <p class='focus-box'>{html.escape(state.progress.next_focus or 'brak')}</p>
          <div class='status-chip'>homework: {html.escape(state.progress.homework_status)}</div>
        </div>
      </div>
    </div>
    """


def build_core_html(state: h.AgentState, user_id: str) -> str:
    directives = "".join(f"<li>{html.escape(rule)}</li>" for rule in state.core.prime_directives)
    return f"""
    <div class='memory-panel'>
      <div class='panel-title'>🏛️ Core Guardrails</div>
      <div class='panel-sub'>Tu mieszkają zasady, które mają być głośniejsze od wygody modelu.</div>
      <div class='split-grid'>
        <div>
          <div class='mini-title'>Dyrektywy</div>
          <ul class='mini-list'>{directives}</ul>
        </div>
        <div>
          <div class='mini-title'>Ostatni test</div>
          <p class='focus-box'>{html.escape(LAST_CORE_REPORT[user_id])}</p>
        </div>
      </div>
    </div>
    """


def build_workflow_html(state: h.AgentState) -> str:
    mapping = {
        h.SessionPhase.HOMEWORK_CHECK: (
            "widzi: check-in, poprzedni fokus, błędy z poprzedniej sesji",
            "nie widzi jeszcze pełnego rozwijania nowego roleplayu",
            "po pierwszym wejściu przejdzie automatycznie do PRACTICE",
        ),
        h.SessionPhase.PRACTICE: (
            f"widzi: temat `{state.workflow.topic}`, high-stakes context i vocabulary targets",
            "nie wchodzi jeszcze w finalne domknięcie sesji",
            "po kilku turach przygotuje WRAP_UP",
        ),
        h.SessionPhase.WRAP_UP: (
            "widzi: podsumowanie, błędy i next step",
            "nie rozwija już nowego wątku od zera",
            "ta faza przygotowuje zapis do pamięci epizodycznej",
        ),
    }
    visible, hidden, next_rule = mapping[state.workflow.phase]
    return f"""
    <div class='memory-panel'>
      <div class='panel-title'>🎯 Workflow + KK</div>
      <div class='panel-sub'>KK działają tutaj jak wewnętrzny reżyser. Użytkownik nie przełącza trybów ręcznie - system sam zawęża pole widzenia modelu.</div>
      <div class='workflow-box'>
        <div><b>Faza teraz:</b> {html.escape(state.workflow.phase.value)}</div>
        <div><b>{html.escape(visible)}</b></div>
        <div><b>{html.escape(hidden)}</b></div>
        <div><b>Następny ruch systemu:</b> {html.escape(next_rule)}</div>
      </div>
    </div>
    """


def build_episodic_html(state: h.AgentState) -> str:
    if not state.episodic.episodes:
        body = "<div class='empty-note'>Brak epizodów. Ta warstwa budzi się po kliknięciu `Zamknij i zapisz`.</div>"
    else:
        blocks = []
        for idx, episode in enumerate(reversed(state.episodic.episodes[-4:]), start=1):
            blocks.append(
                f"""
                <div class='episode-card'>
                  <div class='episode-head'>Epizod {idx}</div>
                  <p>{html.escape(episode.summary)}</p>
                  <div class='episode-next'>next step: {html.escape(episode.next_step or 'brak')}</div>
                </div>
                """
            )
        body = "".join(blocks)
    return f"""
    <div class='memory-panel'>
      <div class='panel-title'>📚 Episodic Memory</div>
      <div class='panel-sub'>Tu sesja przestaje być "logiem". Zostaje skompresowana do rzeczy, które naprawdę mają wrócić.</div>
      <div class='episode-stack'>{body}</div>
    </div>
    """


def build_status_text(user_id: str) -> str:
    state = get_store_state(user_id)
    parts = [
        f"faza: {state.workflow.phase.value}",
        f"working: {len(state.working.history)}",
        f"semantic: {count_semantic_assets(state)}",
        f"epizody: {len(state.episodic.episodes)}",
    ]
    return " | ".join(parts)


def render_all(user_id: str, topic: str):
    state = get_store_state(user_id)
    open_phase = determine_open_phase(state)
    return (
        render_persona_html(state),
        build_guidance_html(state, user_id),
        build_kpi_html(state),
        build_event_html(user_id),
        build_activity_html(user_id),
        build_layer_pulse_html(state, user_id),
        build_working_html(state),
        build_semantic_html(state, user_id),
        build_progress_html(state),
        build_core_html(state, user_id),
        build_workflow_html(state),
        build_episodic_html(state),
        h.preview_next_prompt(state, topic=topic, phase=open_phase),
        LAST_EPISODE_REPORT[user_id],
        render_store_overview_html(),
        wrap_status(build_status_text(user_id)),
    )


def sync_user_view(user_id: str, topic: str):
    return render_all(user_id, topic)


def load_demo_case(case_name: str):
    user_id = "marek_01" if case_name == "marek" else "ania_02"
    topic = DEFAULT_TOPICS[user_id]
    return (user_id, topic, [], "") + render_all(user_id, topic)


def fill_quick_prompt(name: str) -> str:
    return QUICK_PROMPTS[name]


def open_session_ui(user_id: str, topic: str):
    state = get_store_state(user_id)
    phase = determine_open_phase(state)
    h.start_session(state, topic=topic, phase=phase)
    ACTIVE_SESSION[user_id] = True
    LAST_CHANGED_LAYERS[user_id] = {"workflow", "working"}
    if state.progress.next_focus or state.episodic.episodes:
        LAST_SYSTEM_EVENT[user_id] = (
            "### 🧠 PAMIĘĆ PRZYWOŁANA - Coach nie zaczyna od zera\n\n"
            f"- Ostatni fokus: `{state.progress.next_focus or 'brak'}`\n"
            f"- Epizody w pamięci: `{len(state.episodic.episodes)}`\n"
            f"- Coach otworzył nową sesję w fazie `{phase.value}`, żeby wrócić do poprzedniego kontekstu.\n"
        )
        push_activity(user_id, "🧠", "Pamięć przywołana przy starcie nowej sesji.")
    else:
        LAST_SYSTEM_EVENT[user_id] = (
            "### Sesja otwarta\n\n"
            f"- Coach otworzył sesję w fazie `{phase.value}`.\n"
            "- To oznacza, że KK same ustaliły pierwszy zakres widzenia modelu.\n"
        )
        push_activity(user_id, "📂", f"Sesja otwarta w fazie {phase.value}.")
    opener = assistant_opener(state)
    state.working.add("assistant", opener)
    history = [{"role": "assistant", "content": opener}]
    outputs = render_all(user_id, topic)
    return (history,) + outputs


def send_message_ui(user_id: str, topic: str, message: str, history):
    if not message or not message.strip():
        outputs = render_all(user_id, topic)
        return (history or [], "", *outputs[:-1], wrap_status("⚠️ Wpisz wiadomość."))

    state = get_store_state(user_id)
    if not ACTIVE_SESSION[user_id]:
        open_result = open_session_ui(user_id, topic)
        history = open_result[0]
        state = get_store_state(user_id)

    state.workflow.topic = topic
    before = snapshot_state(state)
    try:
        result = h.run_turn_live(state, message)
    except Exception:
        push_activity(user_id, "⚠️", "Chwilowy problem modelu podczas tury.")
        outputs = render_all(user_id, topic)
        return (
            history or [],
            message,
            *outputs[:-1],
            wrap_status("⚠️ Coach miał chwilowy problem z odpowiedzią. Kliknij Wyślij jeszcze raz."),
        )
    auto_phase_note = maybe_advance_phase(state)
    after = snapshot_state(state)

    semantic_write = semantic_write_to_dict(result.semantic_write)
    LAST_SEMANTIC_WRITE[user_id] = semantic_write
    LAST_CORE_REPORT[user_id] = result.core_guardrail_reason or "Guardrail nie był potrzebny w ostatniej turze."
    changed, event = compare_snapshots(
        before,
        after,
        core_reason=result.core_guardrail_reason if result.core_guardrail_used else None,
    )
    if auto_phase_note:
        changed.add("workflow")
        event += f"\n- {auto_phase_note}"
    LAST_CHANGED_LAYERS[user_id] = changed
    LAST_SYSTEM_EVENT[user_id] = event
    if semantic_write:
        push_activity(user_id, "🧠", f"Semantic zyskała {len(semantic_write)} nowych bloków informacji.")
    if result.core_guardrail_used:
        push_activity(user_id, "🛡️", result.core_guardrail_reason or "Zadziałał guardrail Core.")
    if auto_phase_note:
        push_activity(user_id, "🎯", auto_phase_note)
    push_activity(user_id, "⚡", f"Working urosło do {after['working_count']} elementów.")

    history = list(history or [])
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": result.reply})

    outputs = render_all(user_id, topic)
    return (history, "", *outputs[:-1], wrap_status("✅ Tura wykonana. Obserwuj Semantic, Working i Workflow."))


def close_session_ui(user_id: str, topic: str, history):
    state = get_store_state(user_id)
    if not state.working.history:
        outputs = render_all(user_id, topic)
        return (history or [], *outputs[:-1], wrap_status("⚠️ Najpierw otwórz sesję i wyślij choć jedną wiadomość."))

    before = snapshot_state(state)
    try:
        episode = h.close_session(state)
    except Exception:
        push_activity(user_id, "⚠️", "Problem podczas kompresji sesji.")
        outputs = render_all(user_id, topic)
        return (history or [], *outputs[:-1], wrap_status("⚠️ Nie udało się zamknąć sesji. Kliknij ponownie za chwilę."))
    ACTIVE_SESSION[user_id] = False
    next_phase = determine_open_phase(state)
    state.workflow.phase = next_phase
    after = snapshot_state(state)

    LAST_EPISODE_REPORT[user_id] = format_episode_report(episode)
    LAST_SEMANTIC_WRITE[user_id] = {}
    changed, event = compare_snapshots(before, after)
    changed.update({"episodic", "progress"})
    LAST_CHANGED_LAYERS[user_id] = changed
    LAST_SYSTEM_EVENT[user_id] = event + "\n- Sesja została zamknięta, skompresowana i zapisana do pamięci."
    push_activity(user_id, "📚", "Sesja została skompresowana do pamięci epizodycznej.")
    push_activity(user_id, "🧾", f"Ustawiono next focus: {state.progress.next_focus or 'brak'}.")

    outputs = render_all(user_id, topic)
    return ([], *outputs[:-1], wrap_status("✅ Sesja zamknięta i zapisana. Otwórz nową sesję, żeby zobaczyć powrót pamięci."))


def reset_user_ui(user_id: str, topic: str):
    store.reset_user(user_id)
    LAST_EPISODE_REPORT[user_id] = "_Brak zapisanej sesji._"
    LAST_SYSTEM_EVENT[user_id] = "_Jeszcze nic nie uruchomiono._"
    LAST_SEMANTIC_WRITE[user_id] = {}
    LAST_CORE_REPORT[user_id] = "Jeszcze nie testowano zasad core."
    LAST_CHANGED_LAYERS[user_id] = set()
    ACTIVITY_FEED[user_id] = []
    ACTIVE_SESSION[user_id] = False
    push_activity(user_id, "♻️", "Profil użytkownika został zresetowany do punktu startowego.")
    outputs = render_all(user_id, topic)
    return ([], "") + outputs


def export_state_json(user_id: str):
    state = get_store_state(user_id)
    with tempfile.NamedTemporaryFile("w", suffix=f"_{user_id}.json", delete=False, encoding="utf-8") as tmp:
        json.dump(state.model_dump(), tmp, ensure_ascii=False, indent=2)
        return tmp.name


CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root {
  --bg: #0c111b;
  --panel: #121926;
  --panel-2: #171f2d;
  --panel-3: #0f1724;
  --text: #edf3fb;
  --muted: #98a7ba;
  --line: rgba(148, 163, 184, 0.18);
  --accent: #18b48b;
  --accent-2: #50c4ff;
  --accent-3: #f59e0b;
  --danger: #ff6b6b;
}

html, body, .gradio-container, .gradio-container * {
  font-family: "IBM Plex Sans", "Segoe UI", "Noto Sans", system-ui, sans-serif !important;
}

body, .gradio-container {
  background:
    radial-gradient(circle at top left, rgba(24, 180, 139, 0.14), transparent 28%),
    radial-gradient(circle at top right, rgba(80, 196, 255, 0.12), transparent 24%),
    linear-gradient(180deg, #0b1018 0%, #0f1622 100%) !important;
  color: var(--text);
}

.gradio-container {
  max-width: 1540px !important;
  margin: 0 auto;
  padding: 18px 18px 32px;
}

.gradio-container .block,
.gradio-container .form,
.gradio-container .gr-box,
.gradio-container .gr-panel {
  border-color: var(--line) !important;
}

.hero-shell,
.guide-card,
.persona-card,
.memory-panel,
.kpi-card,
.layer-card,
.store-card,
.episode-card {
  background: linear-gradient(180deg, rgba(18, 25, 38, 0.98), rgba(12, 17, 27, 0.98));
  border: 1px solid var(--line);
  box-shadow: 0 18px 40px rgba(3, 8, 18, 0.28);
}

.hero-shell {
  padding: 22px 26px;
  border-radius: 28px;
  margin-bottom: 16px;
}

.hero-eyebrow {
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 8px;
}

.hero-shell h1 {
  margin: 0 0 10px 0;
  font-size: 34px;
  line-height: 1.08;
  color: #fbfdff;
}

.hero-shell p {
  margin: 0;
  max-width: 980px;
  line-height: 1.55;
  color: #dce7f4;
}

.persona-card {
  border-radius: 24px;
  padding: 18px 18px 16px;
  margin-bottom: 14px;
}

.persona-head {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.persona-head h2 {
  margin: 2px 0 0 0;
  font-size: 28px;
  color: #fbfdff;
}

.eyebrow {
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent-2);
}

.level-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 56px;
  height: 36px;
  border-radius: 999px;
  background: rgba(24, 180, 139, 0.16);
  color: #d9fff3;
  font-weight: 700;
}

.persona-goal {
  margin: 0 0 12px 0;
  color: #edf3fb;
  line-height: 1.45;
}

.persona-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.persona-cell {
  border-radius: 16px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(148, 163, 184, 0.12);
}

.persona-cell span {
  display: block;
  color: var(--muted);
  font-size: 11px;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.persona-cell b {
  color: #f8fbff;
  font-weight: 600;
}

.guide-card {
  border-radius: 24px;
  padding: 18px 20px;
  margin-bottom: 14px;
}

.guide-eyebrow {
  color: var(--accent-2);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.guide-headline {
  font-size: 18px;
  font-weight: 700;
  line-height: 1.4;
  color: #f7fbff;
  margin-bottom: 14px;
}

.step-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 8px;
}

.step-pill {
  min-height: 76px;
  padding: 10px 10px 12px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(148, 163, 184, 0.14);
  color: var(--muted);
  font-size: 12px;
  line-height: 1.25;
}

.step-pill span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  margin-bottom: 8px;
  color: #f8fbff;
  font-weight: 700;
}

.step-pill.done {
  background: rgba(24, 180, 139, 0.10);
  color: #d4fff0;
}

.step-pill.current {
  border-color: rgba(80, 196, 255, 0.44);
  background: rgba(80, 196, 255, 0.10);
  color: #eef8ff;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 12px 0;
}

.kpi-card {
  border-radius: 18px;
  padding: 14px 14px 12px;
}

.kpi-label {
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 6px;
}

.kpi-value {
  font-size: 28px;
  font-weight: 700;
  color: #f8fbff;
}

.kpi-value.small {
  font-size: 18px;
  line-height: 1.25;
}

.layer-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.layer-card {
  border-radius: 18px;
  padding: 14px 14px 16px;
}

.layer-card.changed {
  border-color: rgba(24, 180, 139, 0.48);
  box-shadow: 0 0 0 1px rgba(24, 180, 139, 0.22), 0 12px 30px rgba(24, 180, 139, 0.12);
}

.layer-card.dim {
  opacity: 0.54;
  border-style: dashed;
}

.layer-title {
  font-size: 13px;
  color: #dce8f5;
  margin-bottom: 12px;
}

.layer-value {
  font-size: 17px;
  font-weight: 700;
  color: #ffffff;
  line-height: 1.24;
  min-height: 48px;
}

.layer-meta {
  font-size: 12px;
  color: var(--muted);
}

.memory-stage {
  margin-top: 10px;
}

.stage-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--accent-2);
  margin: 6px 0 10px;
}

.scenario-hint {
  margin: 6px 0 10px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}

.memory-panel {
  border-radius: 22px;
  padding: 16px 18px;
  margin-bottom: 12px;
}

.panel-title {
  font-size: 18px;
  font-weight: 700;
  color: #f8fbff;
  margin-bottom: 4px;
}

.panel-sub {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
  margin-bottom: 12px;
}

.memory-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.memory-row {
  border-radius: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(255, 255, 255, 0.03);
}

.memory-row span {
  display: inline-flex;
  margin-bottom: 6px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.memory-row.user span {
  background: rgba(80, 196, 255, 0.16);
  color: #d8f1ff;
}

.memory-row.assistant span {
  background: rgba(24, 180, 139, 0.16);
  color: #d7faea;
}

.memory-row p {
  margin: 0;
  color: #eef4ff;
  line-height: 1.5;
}

.semantic-grid,
.split-grid,
.store-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.semantic-card {
  border-radius: 16px;
  padding: 12px 12px 10px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(148, 163, 184, 0.12);
}

.mini-title {
  font-size: 12px;
  color: #dbe8f6;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 8px;
}

.mini-title.live-title {
  margin-top: 14px;
}

.mini-list {
  margin: 0;
  padding-left: 18px;
  color: #eef4ff;
}

.mini-list li {
  margin-bottom: 6px;
}

.focus-box {
  margin: 0;
  color: #eef4ff;
  line-height: 1.5;
}

.status-chip {
  display: inline-flex;
  margin-top: 10px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  background: rgba(80, 196, 255, 0.12);
  color: #dff5ff;
}

.write-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.write-chip {
  border-radius: 16px;
  padding: 10px 12px;
  background: rgba(24, 180, 139, 0.10);
  border: 1px solid rgba(24, 180, 139, 0.18);
  color: #e8fff8;
  line-height: 1.45;
  position: relative;
}

.write-chip span {
  display: block;
  font-size: 11px;
  color: #8ff2d1;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 6px;
}

@keyframes popNew {
  from { transform: scale(0.92); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

.write-chip.new {
  animation: popNew 0.35s ease;
}

.write-chip.new::after {
  content: "NEW";
  position: absolute;
  top: -6px;
  right: -6px;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  background: #18b48b;
  color: #04120c;
  padding: 2px 6px;
  border-radius: 999px;
  box-shadow: 0 8px 18px rgba(24, 180, 139, 0.22);
}

.activity-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.activity-item {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 10px;
  align-items: start;
  border-radius: 14px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(148, 163, 184, 0.12);
}

.activity-icon {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(24, 180, 139, 0.12);
  font-size: 16px;
}

.activity-time {
  font-size: 11px;
  color: var(--accent-2);
  margin-bottom: 4px;
  letter-spacing: 0.05em;
}

.activity-text {
  color: #eef4ff;
  line-height: 1.42;
}

.workflow-box {
  border-radius: 18px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(148, 163, 184, 0.12);
}

.workflow-box div {
  color: #eef4ff;
  margin-bottom: 8px;
  line-height: 1.45;
}

.episode-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.episode-card {
  border-radius: 16px;
  padding: 12px 14px;
}

.episode-head {
  color: var(--accent-2);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}

.episode-card p,
.episode-next {
  margin: 0;
  color: #eef4ff;
  line-height: 1.45;
}

.store-card {
  border-radius: 16px;
  padding: 12px 14px;
}

.store-head {
  color: #f8fbff;
  font-weight: 700;
  margin-bottom: 8px;
}

.store-head span {
  color: var(--muted);
  font-weight: 500;
  margin-left: 6px;
}

.store-line {
  color: #dce8f6;
  margin-bottom: 6px;
}

.empty-note {
  border-radius: 14px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--muted);
}

.empty-note.slim {
  padding: 10px 12px;
}

.gradio-container .gr-button-primary,
.gradio-container .primary {
  background: linear-gradient(180deg, #18b48b, #10926f) !important;
  border: none !important;
  color: white !important;
}

.gradio-container .gr-button-secondary {
  background: linear-gradient(180deg, #2a3446, #202a3a) !important;
  border: 1px solid rgba(148, 163, 184, 0.18) !important;
  color: #eef4ff !important;
}

.gradio-container .gr-button {
  border-radius: 14px !important;
  font-weight: 600 !important;
}

.gradio-container textarea,
.gradio-container input,
.gradio-container select {
  background: rgba(255, 255, 255, 0.04) !important;
  color: #eff5ff !important;
  border: 1px solid rgba(148, 163, 184, 0.16) !important;
}

.gradio-container label,
.gradio-container .gr-form,
.gradio-container .gr-markdown {
  color: #eef4ff !important;
}

.gradio-container .gr-accordion {
  border-radius: 18px !important;
}

.gradio-container .gr-accordion .label-wrap {
  background: rgba(255, 255, 255, 0.03) !important;
}

.status-shell {
  border-radius: 16px;
  padding: 12px 14px;
  background: linear-gradient(180deg, rgba(18, 25, 38, 0.98), rgba(12, 17, 27, 0.98));
  border: 1px solid var(--line);
}

@media (max-width: 1280px) {
  .step-grid,
  .layer-grid,
  .persona-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
"""


def build_interface():
    theme = gr.themes.Base(
        primary_hue="emerald",
        secondary_hue="cyan",
        neutral_hue="slate",
    )

    default_user = "marek_01"
    default_topic = DEFAULT_TOPICS[default_user]
    default_state = get_store_state(default_user)

    with gr.Blocks(
        title="DWthon Memory - AI Language Coach",
        theme=theme,
        css=CSS,
    ) as demo:
        gr.HTML(
            """
            <div class='hero-shell'>
              <div class='hero-eyebrow'>DWTHON MEMORY · DAY 3</div>
              <h1>AI Language Coach z żywą pamięcią</h1>
              <p>
                To nie jest zwykły chat. To produkt, który pamięta człowieka, jego stawkę biznesową,
                preferencje feedbacku, blokery pewności siebie i postęp między sesjami. Użytkownik ma
                po prostu rozmawiać. KK, workflow i zapis warstw dzieją się w tle.
              </p>
            </div>
            """
        )

        current_user = gr.State(default_user)
        gr.HTML("<div class='stage-label'>Która warstwa właśnie zareagowała</div>")
        pulse_panel = gr.HTML(build_layer_pulse_html(default_state, default_user))

        with gr.Row(equal_height=False):
            with gr.Column(scale=7):
                guidance_panel = gr.HTML(build_guidance_html(default_state, default_user))
                chatbot = gr.Chatbot(label="Rozmowa", height=560, type="messages")
                message = gr.Textbox(
                    label="Twoja wiadomość",
                    placeholder="Kliknij scenariusz albo wpisz własne zdanie po angielsku. Enter = wyślij, Shift+Enter = nowa linia.",
                    lines=3,
                )
                with gr.Row():
                    send_btn = gr.Button("Wyślij", variant="primary", scale=3)
                    open_btn = gr.Button("Otwórz sesję", scale=2)
                    close_btn = gr.Button("Zamknij i zapisz", scale=2)
                    reset_btn = gr.Button("Reset usera", variant="secondary", scale=2)
                gr.HTML(
                    "<div class='scenario-hint'>Te przyciski poniżej tylko wpisują gotowe zdanie. "
                    "Żeby uruchomić turę, kliknij <b>Wyślij</b> albo użyj <b>Enter</b>.</div>"
                )
                with gr.Row():
                    mistake_btn = gr.Button("🗣️ Błąd językowy", scale=2)
                    stakes_btn = gr.Button("🎯 Wysoka stawka", scale=2)
                    human_btn = gr.Button("👤 Ludzki detal", scale=2)
                    core_btn = gr.Button("🛡️ Granica Core", scale=2)
                    continue_btn = gr.Button("🔁 Kontynuacja", scale=2)
                status_line = gr.Markdown(wrap_status("Gotowe. Załaduj personę albo od razu otwórz sesję."))

            with gr.Column(scale=5):
                with gr.Row():
                    marek_btn = gr.Button("⚡ Marek: Sales demo", scale=1)
                    ania_btn = gr.Button("⚡ Ania: Interview", scale=1)
                persona_panel = gr.HTML(render_persona_html(default_state))
                topic = gr.Textbox(value=default_topic, label="Temat sesji")
                kpi_panel = gr.HTML(build_kpi_html(default_state))
                event_panel = gr.HTML(build_event_html(default_user))
                activity_panel = gr.HTML(build_activity_html(default_user))

        with gr.Column(elem_classes=["memory-stage"]):
            gr.HTML("<div class='stage-label'>Co rośnie pod maską</div>")

            with gr.Row(equal_height=False):
                with gr.Column(scale=7):
                    semantic_panel = gr.HTML(build_semantic_html(default_state, default_user))
                with gr.Column(scale=5):
                    working_panel = gr.HTML(build_working_html(default_state))
                    progress_panel = gr.HTML(build_progress_html(default_state))

        gr.Markdown("### 🔧 Pod maską (dla ambitnych)")
        with gr.Accordion("🏛️ Core Guardrails", open=False):
            core_panel = gr.HTML(build_core_html(default_state, default_user))

        with gr.Accordion("🎯 Workflow + KK", open=False):
            workflow_panel = gr.HTML(build_workflow_html(default_state))

        with gr.Accordion("📚 Episodic Memory", open=False):
            episodic_panel = gr.HTML(build_episodic_html(default_state))

        with gr.Accordion("🧾 Last Session Report", open=False):
            episode_panel = gr.Markdown("_Brak zapisanej sesji._")

        with gr.Accordion("🪄 Prompt Preview", open=False):
            prompt_panel = gr.Textbox(
                value=h.preview_next_prompt(default_state, topic=default_topic, phase=determine_open_phase(default_state)),
                lines=18,
                interactive=False,
                label="To właśnie zobaczy model przy starcie następnej sesji",
            )

        with gr.Accordion("🗂️ Store Overview + Export", open=False):
            store_panel = gr.HTML(render_store_overview_html())
            export_btn = gr.Button("📥 Export state JSON", size="sm")
            export_file = gr.File(label="Pobierz plik")

        panel_outputs = [
            persona_panel,
            guidance_panel,
            kpi_panel,
            event_panel,
            activity_panel,
            pulse_panel,
            working_panel,
            semantic_panel,
            progress_panel,
            core_panel,
            workflow_panel,
            episodic_panel,
            prompt_panel,
            episode_panel,
            store_panel,
            status_line,
        ]

        marek_btn.click(
            fn=lambda: load_demo_case("marek"),
            inputs=None,
            outputs=[current_user, topic, chatbot, message, *panel_outputs],
        )

        ania_btn.click(
            fn=lambda: load_demo_case("ania"),
            inputs=None,
            outputs=[current_user, topic, chatbot, message, *panel_outputs],
        )

        mistake_btn.click(fn=lambda: fill_quick_prompt("mistake"), inputs=None, outputs=[message])
        stakes_btn.click(fn=lambda: fill_quick_prompt("high_stakes"), inputs=None, outputs=[message])
        human_btn.click(fn=lambda: fill_quick_prompt("human_detail"), inputs=None, outputs=[message])
        core_btn.click(fn=lambda: fill_quick_prompt("core"), inputs=None, outputs=[message])
        continue_btn.click(fn=lambda: fill_quick_prompt("continue"), inputs=None, outputs=[message])

        open_btn.click(
            fn=open_session_ui,
            inputs=[current_user, topic],
            outputs=[chatbot, *panel_outputs],
        )

        send_btn.click(
            fn=send_message_ui,
            inputs=[current_user, topic, message, chatbot],
            outputs=[chatbot, message, *panel_outputs],
        )

        message.submit(
            fn=send_message_ui,
            inputs=[current_user, topic, message, chatbot],
            outputs=[chatbot, message, *panel_outputs],
        )

        close_btn.click(
            fn=close_session_ui,
            inputs=[current_user, topic, chatbot],
            outputs=[chatbot, *panel_outputs],
        )

        reset_btn.click(
            fn=reset_user_ui,
            inputs=[current_user, topic],
            outputs=[chatbot, message, *panel_outputs],
        )

        topic.submit(
            fn=sync_user_view,
            inputs=[current_user, topic],
            outputs=panel_outputs,
        )

        export_btn.click(
            fn=export_state_json,
            inputs=[current_user],
            outputs=[export_file],
        )

    demo.queue()
    return demo


def build_fastapi_app():
    demo = build_interface()
    app = FastAPI(root_path=ROOT_PATH)
    app = gr.mount_gradio_app(app, demo, path="/", root_path=ROOT_PATH)
    return app


async def serve():
    app = build_fastapi_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    print("=" * 72)
    print("DWthon Memory - AI Language Coach")
    print("=" * 72)
    print(f"Port: {PORT}")
    print(f"Root path: {ROOT_PATH or '/'}")
    print("Produkt: coach z żywą pamięcią, automatycznym KK i żywym systemem warstw")
    print("Otwórz proxy w przeglądarce; proces ma działać dalej i nie zakończy się sam.")
    print("Zatrzymujesz go ręcznie: Stop w Jupyterze albo Ctrl+C w terminalu.")
    print("=" * 72)
    asyncio.run(serve())
