from __future__ import annotations

import html
import os

import gradio as gr
from dotenv import load_dotenv

from day3_runtime import MAX_TURNS, answer_human, initial_demo_state, policy_alerts, run_demo_turn

load_dotenv()
PORT = int(os.getenv("DWTHON_ADAPTIVE_PORT", "7860"))


def esc(value: object) -> str:
    return html.escape(str(value))


def observation_text(observation: dict) -> str:
    kind = observation.get("kind")
    if kind == "search_hn":
        results = observation.get("results", [])
        return "<br>".join(
            f"<a href='{esc(row['url'])}' target='_blank'>{esc(row['title'])}</a> "
            f"<small>{'[STALE] ' if row.get('stale') else ''}{esc(row['created_at'][:10])} · {esc(row['points'])} pkt · {esc(row['comments'])} komentarzy</small>"
            for row in results
        ) or "Brak wyników."
    if kind == "read_thread":
        thread = observation.get("thread", {})
        comments = thread.get("comments", [])
        return "<br>".join(f"<b>{esc(c['author'])}:</b> {esc(c['text'])}" for c in comments[:3]) or esc(observation.get("error") or observation.get("alert") or "Wątek nie zawiera dostępnych komentarzy.")
    if kind == "tool_error":
        return esc(observation.get("error", "Nie udało się wykonać akcji."))
    if kind == "fetch_source":
        summary = observation.get("summary") or observation.get("error", "Brak czytelnej treści źródła.")
        if observation.get("demo"):
            return f"<b>Materiał scenariusza DEMO</b><br>{esc(summary)}"
        return f"<a href='{esc(observation['url'])}' target='_blank'>Otwórz źródło</a><br>{esc(summary)}"
    if kind == "ask_human":
        options = " · ".join(esc(option) for option in observation.get("options", []))
        return f"<b>Robot pyta:</b> {esc(observation.get('question', ''))}<br><small>Opcje: {options}</small>"
    return esc(observation.get("reason") or observation.get("note") or observation)


def render_contract(state) -> str:
    return f"""<section class='panel contract'>
      <div class='eyebrow'>KROK 0 · KONTRAKT</div>
      <h2>{esc(state.question)}</h2>
      <p class='mode'>WSPÓLNY SCENARIUSZ DEMO</p>
      <div class='facts'><div><small>HORYZONT</small><b>{state.horizon_days} dni</b><i>{esc(state.contract.horizon_days.source)}</i></div>
      <div><small>STANDARD</small><b>{esc(state.contract.evidence_standard.value)}</b><i>{esc(state.contract.evidence_standard.source)}</i></div>
      <div><small>BUDŻET</small><b>{MAX_TURNS} tur</b><i>default</i></div></div>
      <p><b>Frazy HN:</b> {esc(', '.join(state.contract.search_terms.value))} <i>{esc(state.contract.search_terms.source)}</i></p>
      <p>Człowiek określa granicę. Robot dobiera drogę.</p></section>"""


def render_plan(state) -> str:
    rows = "".join(
        f"<div class='plan {esc(step.status)}'><b>{esc(step.id)}</b><span>{esc(step.question)}{'<em>Powód: ' + esc(step.reason) + '</em>' if step.reason else ''}</span><small>{esc(step.status)}</small></div>"
        for step in state.plan
    )
    return f"<section class='panel'><div class='eyebrow'>PLAN v1 → vN</div><h2>Pytania badawcze</h2><p>Plan nie jest listą kroków. Jest hipotezą.</p>{rows}</section>"


def render_trace(state, active: str | None = None) -> str:
    cards = []
    for number, turn in enumerate(state.history, 1):
        decision = turn["next_step"]
        priority = next((step.question for step in state.plan if step.id == decision["current_priority"]), decision["current_priority"])
        changes = "<br>".join(esc(change) for change in turn["changes"]) or "bez zmiany planu"
        patch_reason = decision.get("plan_patch", {}).get("reason") if decision.get("plan_patch") else ""
        turning_point = f"<p class='turning-point'><small>PUNKT ZWROTNY</small> {esc(patch_reason)}</p>" if patch_reason else ""
        api = turn.get("api_decision")
        api_choice = ""
        if api:
            rejected_calls = "".join(
                f"<p class='api-rejected'><small>API CALL · KOD ODRZUCIŁ:</small> <b>{esc(item['selected_action'])}</b> <span>z: {esc(item['choices'])}</span><br>{esc(item['reason'])}</p>"
                for item in turn.get("rejected_proposals", [])
            )
            api_choice = rejected_calls + f"<p class='api-choice'><small>API CALL · MODEL WYBRAŁ:</small> <b>{esc(api['selected_action'])}</b> <span>z: {esc(' | '.join(turn['api_choices']))}</span><br>{esc(api['rationale'])}</p>"
        rejected = turn["observation"].get("rejected")
        attempted = f"<p class='rejected'><b>Robot chciał:</b> {esc(decision['next_action']['action'])}. <b>Kod:</b> {esc(turn['observation']['reason'])}</p>" if rejected else ""
        cards.append(f"""<article class='turn'>
          <div class='turn-head'><b>TURA {number}</b><span>{esc(decision['next_action']['action'])}</span></div>
          <h3>{esc(decision['situation_analysis'])}</h3>
          {api_choice}
          {turning_point}
          <p class='priority'><small>AKTUALNY PRIORYTET:</small> {esc(priority)}</p>
          <div class='split'><div><small>CO ZMIENIŁO SIĘ W PLANIE</small><p>{changes}</p></div>
          <div><small>CO ROBOT ZAOBSERWOWAŁ</small><p>{observation_text(turn['observation'])}</p></div></div>
          {attempted}
          <p class='allowed'><small>PO TEJ OBSERWACJI ROBOT MOŻE:</small> {esc(' | '.join(turn['allowed_actions']))}</p>
          <details><summary>Co robot widział przed decyzją?</summary><pre>{esc(turn['state_view'])}</pre></details>
        </article>""")
    active_card = ""
    if active:
        active_card = f"""<article class='turn active'><div class='pulse'></div><div>
        <div class='turn-head'><b>W TOKU</b><span>robot pracuje</span></div>
        <h3>{esc(active)}</h3><p>Ta tura pojawi się na osi, gdy akcja zwróci obserwację.</p></div></article>"""
    if not cards and not active_card:
        return "<section class='panel empty'><h2>Droga robota</h2><p>Po uruchomieniu tu pojawi się każda decyzja: obserwacja → decyzja → zmiana planu → kolejna akcja.</p></section>"
    return "<section class='panel trace'><div class='eyebrow'>OŚ DECYZJI</div><h2>Droga robota</h2>" + "".join(cards) + active_card + "</section>"


def evidence_source(item: dict) -> str:
    if item.get("demo"):
        return "<span class='demo-source'>materiał scenariusza DEMO</span>"
    return f"<a href='{esc(item['source_url'])}' target='_blank'>źródło</a>"


def render_evidence(state) -> str:
    if not state.evidence:
        body = "<p class='zero'>0 dowodów. Robot musi przeczytać wątek albo źródło.</p>"
    else:
        body = "".join(
            f"<article class='evidence'><small>{esc(item['kind'])} · {esc(item['supports_step'])}</small><p>{esc(item['claim'])}</p>{evidence_source(item)}</article>"
            for item in state.evidence[-7:]
        )
    alerts = "".join(f"<li>{esc(alert)}</li>" for alert in policy_alerts(state))
    return f"<section class='panel evidence-panel'><div class='eyebrow'>DOWODY I GRANICE</div><h2>Na czym robot opiera decyzję?</h2>{body}<h3>Alerty polityki</h3><ul>{alerts or '<li>brak</li>'}</ul></section>"


def render_report(state) -> str:
    finish = next((x["observation"] for x in reversed(state.history) if x["observation"].get("kind") == "finish"), None)
    turning_points = sum(bool(x["next_step"].get("plan_patch")) for x in state.history)
    if finish:
        body = f"<div class='recommendation'>{esc(finish['recommendation']).upper()}</div><p>{esc(finish['reason'])}</p>"
    else:
        body = "<div class='recommendation pending'>W TOKU</div><p>Robot zebrał sygnały, ale jeszcze nie zakończył decyzji.</p>"
    return f"""<section class='panel report'><div class='eyebrow'>RAPORT ROBOTA</div><h2>Wniosek, nie tylko lista linków</h2>{body}
    <div class='facts'><div><small>TURY</small><b>{len(state.history)} / {MAX_TURNS}</b></div><div><small>PUNKTY ZWROTNE</small><b>{turning_points}</b></div><div><small>OTWARTE PYTANIA</small><b>{sum(x.status == 'open' for x in state.plan)}</b></div></div>
    <h3>Czego robot nie wie</h3><p>{esc(', '.join(finish.get('unresolved_steps', [])) if finish else 'HN pokazuje sygnały społeczności technicznej. Nie zastępuje danych o Twoim zespole i procesie.')}</p></section>"""


def views(state, status: str, active: str | None = None):
    question = state.pending_question
    question_html = ""
    choices = gr.update(choices=[], value=None, visible=False)
    visible = gr.update(visible=False)
    if question:
        question_html = f"<section class='human'><div class='eyebrow'>POTRZEBUJĘ TWOJEJ DECYZJI</div><h3>{esc(question.question)}</h3><p>Wybierz jedną opcję. Robot zachowa całą historię i wznowi pracę.</p></section>"
        choices = gr.update(choices=question.options, value=question.options[0] if question.options else None, visible=True)
        visible = gr.update(visible=True)
    return (
        render_contract(state), render_plan(state), render_trace(state, active), render_evidence(state),
        render_report(state), status, state, question_html, choices, visible,
    )


def run_robot():
    """Generator dla Gradio.

    Każda tura jest pełnym, zwalidowanym NextStep. Nie czekamy na koniec
    całego zadania: po każdej turze UI dostaje aktualny kontrakt, plan,
    obserwację i ślad decyzji.
    """
    state = initial_demo_state()
    yield views(state, "⚡ Robot zaczyna. Pierwsza decyzja pojawi się za chwilę.")
    for _ in range(MAX_TURNS):
        next_number = len(state.history) + 1
        yield views(state, f"🧠 Tura {next_number}/{MAX_TURNS}: robot analizuje, co ma zrobić dalej.", f"Tura {next_number}: analizuję stan, plan i wcześniejsze obserwacje. Wybieram następną decyzję.")
        try:
            step, _, _ = run_demo_turn(state)
        except Exception as error:
            yield views(state, f"⚠️ API Call nie został wykonany: {error}")
            return
        if step is None:
            yield views(state, "✅ Demonstracja została zakończona.")
            return
        action_label = {
            "search_hn": "przeszukuję Hacker News",
            "read_thread": "czytam wątek i komentarze",
            "fetch_source": "czytam źródło",
            "ask_human": "przygotowuję pytanie do człowieka",
            "finish": "przygotowuję Raport Robota",
        }.get(step.next_action.action, step.next_action.action)
        yield views(state, f"⚙️ Tura {next_number}/{MAX_TURNS}: decyzja jest gotowa. Robot {action_label}.", f"Tura {next_number}: decyzja gotowa. Teraz {action_label}.")
        observation = state.observations[-1]
        status = f"⚡ Tura {len(state.history)} z {MAX_TURNS}: robot wybrał `{step.next_action.action}`."
        if observation.get("kind") == "finish":
            status = "✅ Robot zakończył przebieg i przygotował raport."
        elif observation.get("kind") == "ask_human":
            status = "✋ Robot potrzebuje jednej decyzji człowieka. Odpowiedź zmieni miarę sukcesu pilota, a nie zresetuje przebieg."
        yield views(state, status)
        if observation.get("kind") in {"finish", "ask_human"}:
            return
    yield views(state, "⚡ Robot wykorzystał budżet 7 tur. Następna wersja wymusi uczciwe zakończenie albo pytanie do człowieka.")


def continue_after_human(state, answer: str):
    if state is None or not answer:
        yield views(initial_demo_state(), "⚠️ Najpierw uruchom robota i wybierz odpowiedź.")
        return
    answer_human(state, answer)
    yield views(state, "✅ Odpowiedź człowieka trafiła do stanu. Robot wznawia pracę.")
    for _ in range(MAX_TURNS - len(state.history)):
        number = len(state.history) + 1
        yield views(state, f"🧠 Tura {number}/{MAX_TURNS}: robot analizuje odpowiedź człowieka.", f"Tura {number}: wykorzystuję nowy kontekst i wybieram dalszą drogę.")
        try:
            step, _, _ = run_demo_turn(state, answer)
        except Exception as error:
            yield views(state, f"⚠️ API Call nie został wykonany: {error}")
            return
        if step is None:
            yield views(state, "✅ Demonstracja została zakończona.")
            return
        action_label = {"search_hn": "przeszukuję Hacker News", "read_thread": "czytam wątek i komentarze", "fetch_source": "czytam źródło", "finish": "przygotowuję Raport Robota"}.get(step.next_action.action, step.next_action.action)
        yield views(state, f"⚙️ Tura {number}/{MAX_TURNS}: robot {action_label}.", f"Tura {number}: decyzja gotowa. Teraz {action_label}.")
        observation = state.observations[-1]
        status = "✅ Robot zakończył przebieg i przygotował raport." if observation.get("kind") == "finish" else f"✅ Tura {number}/{MAX_TURNS}: obserwacja została zapisana."
        yield views(state, status)
        if observation.get("kind") in {"finish", "ask_human"}:
            return


CSS = """
:root{--bg:#090a0e;--card:#141620;--line:#303445;--muted:#9ba2b5;--text:#f4f5f8;--purple:#b99cff;--mint:#72f5bd;--orange:#ff6a00}
body,.gradio-container{background:radial-gradient(circle at 88% 0,#3b1f643d,transparent 30%),radial-gradient(circle at 0 0,#0a493c32,transparent 26%),var(--bg)!important;color:var(--text)!important}.gradio-container{max-width:1540px!important;padding:22px!important}.hero{padding:28px;border:1px solid var(--line);border-radius:24px;background:linear-gradient(135deg,#171827,#0e1018)}.hero h1{margin:6px 0;font-size:38px}.hero p,.panel>p{color:var(--muted)}.eyebrow{color:var(--mint);font-size:11px;font-weight:700;letter-spacing:.14em}.panel{background:linear-gradient(180deg,#171925,#101119);border:1px solid var(--line);padding:18px;border-radius:16px;margin:8px 0}.panel h2{margin:5px 0 8px;font-size:21px}.contract h2{font-size:18px}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}.facts div{padding:10px;border-radius:10px;background:#ffffff08}.facts small{display:block;color:var(--muted);font-size:10px}.facts b{display:block;font-size:15px}.facts i{color:var(--mint);font-size:10px;font-style:normal}.plan{display:grid;grid-template-columns:35px 1fr auto;gap:8px;padding:10px;margin:6px 0;border-radius:10px;background:#ffffff08}.plan b{color:var(--purple)}.plan small{color:var(--mint)}.plan.closed{opacity:.6}.plan.dropped{opacity:.42;text-decoration:line-through}.turn{border-left:4px solid var(--purple);padding:15px 16px;margin:13px 0;border-radius:0 13px 13px 0;background:#ffffff06}.turn-head{display:flex;gap:9px;align-items:center}.turn-head b{color:var(--purple);font-size:12px}.turn-head span{color:var(--mint);font-size:11px;border:1px solid #72f5bd55;padding:2px 6px;border-radius:99px}.turn h3{font-size:16px;margin:9px 0}.split{display:grid;grid-template-columns:1fr 2fr;gap:14px}.split small{color:var(--muted);font-size:10px}.split p{font-size:13px;line-height:1.45}.split a{color:#a9cbff}details{margin-top:8px;color:var(--muted)}pre{white-space:pre-wrap;background:#050609;padding:10px;border-radius:8px;font-size:11px}.report{border-color:#8f70e8}.recommendation{font-size:29px;font-weight:800;color:var(--mint)}.recommendation.pending{color:#ffc45c}.empty{border-style:dashed}.footer{color:var(--muted);font-size:13px;padding:8px}@media(max-width:900px){.facts,.split{grid-template-columns:1fr}}
"""


CSS += """
.turn.active{border-color:var(--orange);background:linear-gradient(90deg,#ff6a001d,#ffffff06);display:flex;gap:14px;align-items:flex-start}
.turn.active h3{color:#ffd0a8}.api-choice,.api-rejected{padding:8px 10px;border-radius:0 8px 8px 0;font-size:13px}.api-choice{border-left:3px solid var(--orange);background:#ff6a0015;color:#f7dfcd}.api-rejected{border-left:3px solid #ff697a;background:#ff697a14;color:#ffd4da}.api-choice small,.api-rejected small{font-weight:800;font-size:10px;letter-spacing:.08em}.api-choice small{color:#ffb36a}.api-rejected small{color:#ff9ca8}.api-choice span,.api-rejected span{color:var(--muted);font-size:11px}.turning-point{border:1px solid #72f5bd55;background:#72f5bd10;padding:8px 10px;border-radius:8px;color:#d5ffe9;font-size:13px}.turning-point small{color:var(--mint);font-weight:800;font-size:10px;letter-spacing:.08em}.pulse{width:13px;height:13px;min-width:13px;border-radius:50%;background:var(--orange);margin-top:7px;box-shadow:0 0 0 0 #ff6a0088;animation:pulse 1.1s infinite}.allowed,.zero,.priority{color:var(--muted);font-size:12px}.rejected{border:1px solid #ff697a66;background:#ff697a14;padding:9px;border-radius:9px;color:#ffd4da}.plan em{display:block;color:var(--muted);font-size:11px;font-style:normal;margin-top:4px}.evidence{border-left:3px solid var(--mint);background:#ffffff08;padding:9px 11px;margin:8px 0;border-radius:0 9px 9px 0}.evidence small{color:var(--mint)}.evidence p{font-size:12px;margin:5px 0}.evidence a{color:#a9cbff;font-size:12px}.human{border:1px solid #ffb36a;background:#ff6a0015;border-radius:14px;padding:14px;margin:10px 0}.human h3{margin:5px 0}.split{min-width:0}.split div{min-width:0;overflow-wrap:anywhere;word-break:break-word}.turn p,.evidence p{overflow-wrap:anywhere}
@keyframes pulse{70%{box-shadow:0 0 0 12px #ff6a0000}100%{box-shadow:0 0 0 0 #ff6a0000}}
button{transition:transform 160ms cubic-bezier(.23,1,.32,1)}button:active{transform:scale(.97)}
@media(prefers-reduced-motion:reduce){.pulse{animation:none}}
"""


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Mój Robot - DWthon Adaptive Planning", css=CSS) as demo:
        gr.HTML("<div class='hero'><div class='eyebrow'>DWTHON ADAPTIVE PLANNING · DZIEŃ 3</div><h1>Mój Robot</h1><p>Przejdź wspólny scenariusz: siedem tur, trzy punkty zwrotne i jedna decyzja, która realnie zmienia zakres pilota.</p></div>")
        run = gr.Button("Uruchom scenariusz Mojego Robota", variant="primary", size="lg")
        status = gr.Markdown("To kontrolowany scenariusz dydaktyczny. Nie udaje researchu na żywo - pokazuje mechanizm adaptive planning bez przypadkowych wyników internetu.")
        state = gr.State(value=None)
        with gr.Row():
            contract = gr.HTML(render_contract(initial_demo_state()))
            plan = gr.HTML(render_plan(initial_demo_state()))
        with gr.Row():
            with gr.Column(scale=7):
                trace = gr.HTML(render_trace(initial_demo_state()))
            with gr.Column(scale=4):
                evidence = gr.HTML(render_evidence(initial_demo_state()))
                report = gr.HTML(render_report(initial_demo_state()))
        human_question = gr.HTML()
        human_answer = gr.Radio([], label="Twoja odpowiedź", visible=False)
        continue_button = gr.Button("Kontynuuj pracę robota", visible=False)
        gr.HTML("<div class='footer'>To jest poziom 5: robot sam decyduje, co sprawdzić dalej. Ty widzisz każdą decyzję i granice, których nie może przekroczyć.</div>")
        outputs = [contract, plan, trace, evidence, report, status, state, human_question, human_answer, continue_button]
        run.click(run_robot, None, outputs)
        continue_button.click(continue_after_human, [state, human_answer], outputs)
    return demo


if __name__ == "__main__":
    build_app().queue().launch(server_name="0.0.0.0", server_port=PORT, share=False)
