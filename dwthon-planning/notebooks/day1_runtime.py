from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from IPython.display import HTML, display


INPUT_DIR = Path(__file__).resolve().parent.parent / "input"
SNAPSHOT_FILE = "ai_code_review_day1_snapshot.json"
REPLAY_FILE = "ai_code_review_day1_replay.json"


def load_day1_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    """Wczytuje dane tylko do odczytu i sprawdza ich minimalny kontrakt."""
    paths = [INPUT_DIR / SNAPSHOT_FILE, INPUT_DIR / REPLAY_FILE]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Brakuje zamrożonych danych Day 1:\\n- "
            + "\\n- ".join(missing)
            + "\\nNie zastępuj ich sztucznym if-em. Wgraj prawdziwy snapshot HN i replay modelu."
        )

    snapshot, replay = (json.loads(path.read_text(encoding="utf-8")) for path in paths)
    required_snapshot = {"captured_at", "question", "horizon_days", "relevance_results", "date_results"}
    required_replay = {"contract", "plan_v1", "turns"}
    if missing_fields := required_snapshot - snapshot.keys():
        raise ValueError(f"Snapshot nie ma pól: {sorted(missing_fields)}")
    if missing_fields := required_replay - replay.keys():
        raise ValueError(f"Replay nie ma pól: {sorted(missing_fields)}")
    return snapshot, replay


def _card(title: str, body: str, accent: str = "#7c3aed") -> str:
    return f"""
    <div style="border:1px solid #3b4252;border-left:5px solid {accent};border-radius:10px;
                padding:18px 20px;margin:10px 0;background:#171923;color:#f4f5f8">
      <div style="font-size:12px;font-weight:700;letter-spacing:1px;color:{accent}">{html.escape(title)}</div>
      <div style="margin-top:10px;line-height:1.55">{body}</div>
    </div>"""


def show_autonomy_levels() -> None:
    rows = [
        ("1-3", "wykonuje instrukcję", "człowiek projektuje trasę"),
        ("4", "wybiera narzędzie w danym kroku", "plan pozostaje stały"),
        ("5", "ocenia plan po każdym wyniku", "cel i granice pozostają po stronie człowieka"),
    ]
    body = "".join(
        f"<tr><td><b>{level}</b></td><td>{does}</td><td>{does_not}</td></tr>"
        for level, does, does_not in rows
    )
    display(HTML(f"""
    <table style="width:100%;border-collapse:collapse">
      <tr><th>Poziom</th><th>Robot robi sam</th><th>Robot nie decyduje</th></tr>
      {body}
    </table>"""))


def show_contract(contract: dict[str, Any]) -> None:
    labels = {
        "decision": "DECYZJA",
        "horizon_days": "HORYZONT",
        "evidence_standard": "STANDARD DOWODU",
        "search_terms": "FRAZY HN",
        "escalation_rule": "ESKALACJA",
    }
    fields = []
    for name, value in contract.items():
        if isinstance(value, dict):
            content = html.escape(str(value.get("value", "")))
            source = html.escape(str(value.get("source", "")))
        else:
            content, source = html.escape(str(value)), ""
        label = labels.get(name, name.replace("_", " ").upper())
        fields.append(
            f"<div style='margin:9px 0'><small style='color:#9ba2b5'>{html.escape(label)}</small><br>"
            f"<b>{content}</b> <code>{source}</code></div>"
        )
    display(HTML(_card("KROK 0 - KONTRAKT", "".join(fields), "#10b981")))


def show_snapshot(snapshot: dict[str, Any]) -> None:
    body = (
        f"<b>Pytanie:</b> {html.escape(snapshot['question'])}<br>"
        f"<b>Dane z:</b> {html.escape(snapshot['captured_at'])}<br>"
        f"<b>Horyzont:</b> {snapshot['horizon_days']} dni<br>"
        "To jest zamrożony snapshot. Każdy uczestnik widzi te same dane."
    )
    display(HTML(_card("DANE", body, "#0ea5e9")))


def _results_html(results: list[dict[str, Any]]) -> str:
    lines = []
    for item in results[:3]:
        title = html.escape(str(item["title"]))
        date = html.escape(str(item["created_at"])[:10])
        points = html.escape(str(item.get("points", "-")))
        comments = html.escape(str(item.get("num_comments", item.get("comments", "-"))))
        lines.append(f"<li><b>{title}</b><br><code>{date}</code> · {points} pkt · {comments} komentarzy</li>")
    return "<ol>" + "".join(lines) + "</ol>"


def show_fixed_report(snapshot: dict[str, Any]) -> None:
    display(HTML(_card(
        "SZTYWNY SKRYPT: RAPORT O NOWOŚCIACH",
        _results_html(snapshot["relevance_results"]),
        "#f97316",
    )))


def _patch_html(patch: dict[str, Any] | None) -> str:
    if not patch:
        return "<b>plan_patch = None</b><br>Plan nadal pasuje do celu."
    operations = patch.get("operations", patch.get("ops", []))
    lines = []
    for op in operations:
        operation = html.escape(str(op.get("op", "zmiana")))
        step = html.escape(str(op.get("id", op.get("step_id", ""))))
        question = html.escape(str(op.get("research_question", op.get("question", ""))))
        lines.append(f"<li><code>{operation}</code> <b>{step}</b> {question}</li>")
    reason = html.escape(str(patch.get("reason", "")))
    return f"<b>Powód:</b> {reason}<ul>{''.join(lines)}</ul>"


def show_next_step(turn: dict[str, Any]) -> None:
    action = turn.get("next_action", turn.get("action", {}))
    action_name = action.get("action", action) if isinstance(action, dict) else action
    action_details = ""
    if isinstance(action, dict):
        parameters = [f"{key}={value!r}" for key, value in action.items() if key != "action"]
        action_details = f"({html.escape(', '.join(parameters))})" if parameters else ""
    body = (
        f"<b>Co zobaczył:</b> {html.escape(str(turn.get('situation_analysis', '')))}<br>"
        f"<b>Priorytet:</b> <code>{html.escape(str(turn.get('current_priority', '')))}</code><br>"
        f"<b>Jedna akcja:</b> <code>{html.escape(str(action_name))}{action_details}</code><hr>"
        f"{_patch_html(turn.get('plan_patch'))}"
    )
    display(HTML(_card("NEXT STEP - PRAWDZIWA DECYZJA MODELU", body, "#a78bfa")))


def show_experiment(snapshot: dict[str, Any], replay: dict[str, Any]) -> None:
    """Kontrastuje trasę sztywną z prawdziwą decyzją z nagranego trace'a."""
    first_turn = replay["turns"][0]
    fixed = _results_html(snapshot["relevance_results"])
    action = first_turn.get("next_action", {})
    action_name = action.get("action", "?") if isinstance(action, dict) else action
    parameters = [] if not isinstance(action, dict) else [f"{key}={value!r}" for key, value in action.items() if key != "action"]
    action_details = f"({html.escape(', '.join(parameters))})" if parameters else ""
    adaptive = (
        f"<b>Obserwacja:</b> {html.escape(str(first_turn.get('situation_analysis', '')))}<br>"
        f"<b>Decyzja:</b> <code>{html.escape(str(action_name))}{action_details}</code><br>"
        f"{_patch_html(first_turn.get('plan_patch'))}"
    )
    display(HTML(f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      {_card("SZTYWNY SKRYPT", fixed, "#f97316")}
      {_card("ROBOT: NEXT STEP", adaptive, "#10b981")}
    </div>"""))


def show_plan_patch(replay: dict[str, Any]) -> None:
    turn = next((turn for turn in replay["turns"] if turn.get("plan_patch")), None)
    if turn is None:
        raise ValueError("Replay Day 1 nie zawiera PlanPatch.")
    show_next_step(turn)


def show_no_change(replay: dict[str, Any]) -> None:
    turn = next((turn for turn in replay["turns"] if turn.get("plan_patch") is None), None)
    if turn is None:
        raise ValueError("Replay Day 1 nie zawiera świadomego plan_patch=None.")
    show_next_step(turn)


def show_trace(replay: dict[str, Any]) -> None:
    cards = []
    for number, turn in enumerate(replay["turns"], start=1):
        action = turn.get("next_action", {})
        action_name = action.get("action", "?") if isinstance(action, dict) else action
        cards.append(_card(
            f"TURA {turn.get('number', number)} - {action_name}",
            f"{html.escape(str(turn.get('situation_analysis', '')))}<br>{_patch_html(turn.get('plan_patch'))}",
            "#a78bfa",
        ))
    display(HTML("".join(cards)))


def show_report_preview(replay: dict[str, Any]) -> None:
    report = replay.get("final_report", {})
    if not report:
        display(HTML(_card("RAPORT ROBOTA", "Raport zobaczysz po przejściu trace'a.", "#10b981")))
        return
    body = "<br>".join(
        f"<b>{html.escape(str(key))}:</b> {html.escape(str(value))}"
        for key, value in report.items()
    )
    display(HTML(_card("RAPORT ROBOTA", body, "#10b981")))
