from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal, Union
from urllib.parse import urlparse

import instructor
import requests
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, create_model

load_dotenv()
client = instructor.patch(OpenAI(base_url="https://llm-api.dataworkshop.eu"), mode=instructor.Mode.MD_JSON)
MAX_TURNS = 7
MAX_CONSECUTIVE_SEARCHES = 2
SOURCE_POLICY = {"github.com": "primary_source", "www.coderabbit.ai": "vendor_claim", "coderabbit.ai": "vendor_claim"}


class PlanStep(BaseModel):
    id: str
    question: str
    status: Literal["open", "closed", "dropped"] = "open"
    reason: str | None = None


class ExtractedEvidence(BaseModel):
    kind: Literal["opinion", "counterargument", "case_claim", "primary_source", "vendor_claim"]
    claim: str
    quote: str
    supports_step: str


class EvidenceExtraction(BaseModel):
    evidence: list[ExtractedEvidence]


class AddStep(BaseModel):
    op: Literal["add"]
    question: str
    reason: str


class DropStep(BaseModel):
    op: Literal["drop"]
    step_id: str
    reason: str


class CloseStep(BaseModel):
    op: Literal["close"]
    step_id: str
    reason: str


class PlanPatch(BaseModel):
    reason: str
    operations: list[Union[AddStep, DropStep, CloseStep]]


class SearchHn(BaseModel):
    action: Literal["search_hn"]
    query: str


class ReadThread(BaseModel):
    action: Literal["read_thread"]
    item_id: str


class FetchSource(BaseModel):
    action: Literal["fetch_source"]
    url: str


class AskHuman(BaseModel):
    action: Literal["ask_human"]
    question: str
    options: list[str]


class Finish(BaseModel):
    action: Literal["finish"]
    recommendation: Literal["działaj", "zbadaj", "obserwuj", "odpuść", "brak podstaw"]
    reason: str
    unresolved_steps: list[str]


class NextStep(BaseModel):
    situation_analysis: str
    current_priority: str
    plan_patch: PlanPatch | None
    next_action: Union[SearchHn, ReadThread, FetchSource, AskHuman, Finish]


class FinishOnly(BaseModel):
    situation_analysis: str
    current_priority: str
    plan_patch: Literal[None]
    next_action: Finish


class DemoApiDecision(BaseModel):
    """Jawny, audytowalny skrót uzasadnienia - nie ukryty chain of thought."""
    selected_action: str
    rationale: str = Field(description="Jedno lub dwa zdania: jakie dane uzasadniają wybór.")


@dataclass
class ContractField:
    value: Any
    source: str


@dataclass
class ResearchContract:
    decision: ContractField
    horizon_days: ContractField
    evidence_standard: ContractField
    search_terms: ContractField
    escalation_rule: ContractField


@dataclass
class RobotState:
    contract: ResearchContract
    plan: list[PlanStep]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    consecutive_searches: int = 0
    seen_queries: list[str] = field(default_factory=list)
    seen_item_ids: list[str] = field(default_factory=list)
    seen_source_urls: list[str] = field(default_factory=list)
    material_reads: int = 0
    consecutive_rejections: int = 0
    pending_question: AskHuman | None = None
    human_context: list[str] = field(default_factory=list)
    mode: Literal["demo", "live"] = "live"
    demo_index: int = 0

    @property
    def question(self): return self.contract.decision.value
    @property
    def horizon_days(self): return self.contract.horizon_days.value


def call_api(messages, response_model):
    return client.chat.completions.create(model="gpt-4o-mini", messages=messages, response_model=response_model, temperature=0.0, max_retries=2)


def initial_state(question: str, horizon_days: int) -> RobotState:
    terms = ["AI code review", "LLM code review", "automated code review"] if "code review" in question.lower() else [question, "AI engineering"]
    contract = ResearchContract(ContractField(question, "user"), ContractField(horizon_days, "user"), ContractField("co najmniej 1 źródło pierwotne i 1 kontrargument", "default"), ContractField(terms, "inferred"), ContractField("zapytaj tylko, gdy odpowiedź zmieni dalszą decyzję", "default"))
    return RobotState(contract, [PlanStep(id="s1", question="Jakie świeże sygnały i praktyczne problemy zgłasza społeczność?"), PlanStep(id="s2", question="Czy poza opiniami istnieje dowód realnego zastosowania?"), PlanStep(id="s3", question="Jaki mały eksperyment najbardziej zmniejszy ryzyko decyzji?")])


def initial_demo_state() -> RobotState:
    """Wspólny, zapisany scenariusz Day 3.

    Jest oznaczony jako DEMO: nie udaje bieżącego internetu ani decyzji modelu
    na żywo. Jego zadaniem jest czytelnie pokazać trzy zachowania poziomu 5.
    """
    state = initial_state("Czy warto zrobić 30-dniowy spike AI code review?", 30)
    state.mode = "demo"
    state.contract.evidence_standard = ContractField("co najmniej 1 karta dowodowa i 1 kontrargument", "scenariusz")
    state.plan[2] = PlanStep(id="s3", question="Jakie narzędzia AI code review są dostępne?")
    state.plan.append(PlanStep(id="s4", question="Jaki mały eksperyment najbardziej zmniejszy ryzyko decyzji?"))
    return state


def _demo_evidence(kind: str, claim: str, step: str) -> dict[str, str]:
    return {"kind": kind, "claim": claim, "quote": claim, "source_url": "materiał scenariusza DEMO", "supports_step": step, "demo": True}


"""Legacy scenario retained below only while replacing it with the readable version.
    return [
        {"analysis": "Najpierw zbieram trzy sygnały. To jeszcze nie są dowody: wybieram materiał do przeczytania, zamiast udawać, że sam wynik wyszukiwania rozstrzyga decyzję.", "priority": "s1", "action": {"action": "search_hn", "query": "AI code review"}, "observation": {"kind": "search_hn", "results": [{"id": "demo-thread-1", "title": "Code reviews in the age of AI", "url": "https://news.ycombinator.com/", "points": 42, "comments": 18, "created_at": "2026-09-08T09:00:00Z"}, {"id": "demo-thread-2", "title": "Why our AI review suggestions created noise", "url": "https://news.ycombinator.com/", "points": 31, "comments": 12, "created_at": "2026-09-07T12:00:00Z"}, {"id": "demo-thread-3", "title": "Small pull requests and automated review", "url": "https://news.ycombinator.com/", "points": 19, "comments": 7, "created_at": "2026-09-06T16:00:00Z"}]}},
        {"analysis": "Są świeże sygnały, ale sam ranking HN nie jest dowodem. Najpierw czytam wątek z praktyczną dyskusją.", "priority": "s1", "action": {"action": "read_thread", "item_id": "demo-thread-1"}, "observation": {"kind": "read_thread", "thread": {"comments": [{"author": "maintainer", "text": "For small pull requests with clear tests, suggestions are useful. On large changes the noise becomes overwhelming."}]}, "evidence": [_demo_evidence("opinion", "For small pull requests with clear tests, suggestions are useful.", "s1"), _demo_evidence("counterargument", "On large changes the noise becomes overwhelming.", "s1")]}},
        {"analysis": "Problemem nie jest już katalog narzędzi. Dowody wskazują, że wartość zależy od wielkości i jakości zmian, więc zmieniam pytanie badawcze.", "priority": "s3", "patch": {"reason": "Sygnał i szum są ważniejsze niż sama lista narzędzi.", "operations": [{"op": "drop", "step_id": "s3", "reason": "Katalog narzędzi nie odpowiada, czy pilot ograniczy szum."}, {"op": "add", "question": "Dla jakich klas zmian AI code review zmniejsza szum zamiast go zwiększać?", "reason": "To ograniczenie wynika z obserwacji."}]}, "action": {"action": "fetch_source", "url": "https://github.com/demo/ai-review-evaluation"}, "observation": {"kind": "fetch_source", "url": "https://github.com/demo/ai-review-evaluation", "summary": "Dokumentacja pilota opisuje użycie sugestii dla małych, dobrze testowanych zmian."}, "evidence": [_demo_evidence("primary_source", "Dokumentacja pilota opisuje użycie sugestii dla małych, dobrze testowanych zmian.", "s2")]}},
        {"analysis": "Źródło pierwotne potwierdza kierunek: małe, dobrze testowane PR-y. Plan nie wymaga kolejnej zmiany, potrzebuję jeszcze niezależnego ograniczenia.", "priority": "s4", "action": {"action": "read_thread", "item_id": "demo-thread-2"}, "observation": {"kind": "read_thread", "thread": {"comments": [{"author": "reviewer", "text": "We stopped trusting suggestions blindly because false positives distracted reviewers and security-sensitive changes needed human review."}]}, "evidence": [_demo_evidence("counterargument", "False positives distracted reviewers and security-sensitive changes needed human review.", "s4")]}},
        {"analysis": "Mamy dowód oraz kontrargument. Dodaję pytanie o miarę sukcesu, bo bez niej pilot będzie tylko kolejną opinią.", "priority": "s4", "patch": {"reason": "Pilot potrzebuje mierzalnego warunku sukcesu.", "operations": [{"op": "add", "question": "Jak zmierzymy, czy AI skraca review bez zwiększania fałszywych pozytywów?", "reason": "Kontrargument pokazał ryzyko szumu."}]}, "action": {"action": "ask_human", "question": "Co dla Was jest ważniejsze w pilocie: mniej szumu dla reviewerów czy wykrywanie większej liczby ryzyk?", "options": ["Mniej szumu dla reviewerów", "Wykrywanie większej liczby ryzyk"]}, "observation": {"kind": "ask_human", "question": "Co dla Was jest ważniejsze w pilocie: mniej szumu dla reviewerów czy wykrywanie większej liczby ryzyk?", "options": ["Mniej szumu dla reviewerów", "Wykrywanie większej liczby ryzyk"]}}},
        {"analysis": f"Człowiek wybrał: {answer or 'Mniej szumu dla reviewerów'}. Konkretyzuję pilot bez zmiany celu.", "priority": "s5", "patch": {"reason": "Odpowiedź człowieka wybiera miarę sukcesu.", "operations": [{"op": "close", "step_id": "s1", "reason": "Sygnały zostały zebrane."}, {"op": "close", "step_id": "s2", "reason": "Mamy źródło pierwotne."}]}, "action": {"action": "fetch_source", "url": "https://github.com/demo/pilot-metrics"}, "observation": {"kind": "fetch_source", "url": "https://github.com/demo/pilot-metrics", "summary": "Pilot: 30 dni, małe PR-y, sugestie jako komentarze, bez automatycznego merge. Miary: czas review, akceptacja sugestii i fałszywe pozytywy."}} ,
        {"analysis": "Mamy wystarczające podstawy do rekomendacji ograniczonego pilota, ale nie do wdrożenia globalnego.", "priority": "s4", "action": {"action": "finish", "recommendation": "zbadaj", "reason": "Uruchom 30-dniowy pilot dla małych, dobrze testowanych PR-ów. Mierz czas review, akceptację sugestii i fałszywe pozytywy. Nie wdrażaj automatycznego merge.", "unresolved_steps": ["s4", "s5"]}, "observation": {"kind": "finish", "recommendation": "zbadaj", "reason": "Uruchom 30-dniowy pilot dla małych, dobrze testowanych PR-ów. Mierz czas review, akceptację sugestii i fałszywe pozytywy. Nie wdrażaj automatycznego merge.", "unresolved_steps": ["s4", "s5"]}} ,
    ]


"""


def _demo_turns(answer: str | None = None) -> list[dict[str, Any]]:
    chosen_metric = answer or "Mniej szumu dla reviewerów"
    noise_first = chosen_metric == "Mniej szumu dla reviewerów"
    pilot_scope = "małe, dobrze testowane PR-y" if noise_first else "PR-y dotyczące autoryzacji i bezpieczeństwa"
    pilot_scope_for = "małych, dobrze testowanych PR-ów" if noise_first else "PR-ów dotyczących autoryzacji i bezpieczeństwa"
    pilot_guardrail = "sugestie są komentarzami, bez automatycznego merge" if noise_first else "każdą sugestię zatwierdza doświadczony reviewer, bez automatycznego merge"
    pilot_metric = "czas review, odsetek zaakceptowanych sugestii i fałszywe pozytywy" if noise_first else "liczba zweryfikowanych ryzyk bezpieczeństwa oraz fałszywe pozytywy"
    return [
        {
            "analysis": "Wynik wyszukiwania to sygnał, nie dowód. Zbieram trzy tropy, aby zdecydować, co przeczytać.",
            "priority": "s1",
            "action": {"action": "search_hn", "query": "AI code review"},
            "observation": {"kind": "search_hn", "results": [
                {"id": "demo-thread-1", "title": "Code reviews in the age of AI", "url": "https://news.ycombinator.com/", "points": 42, "comments": 18, "created_at": "2026-09-08T09:00:00Z"},
                {"id": "demo-thread-2", "title": "Why our AI review suggestions created noise", "url": "https://news.ycombinator.com/", "points": 31, "comments": 12, "created_at": "2026-09-07T12:00:00Z"},
                {"id": "demo-thread-3", "title": "Small pull requests and automated review", "url": "https://news.ycombinator.com/", "points": 19, "comments": 7, "created_at": "2026-09-06T16:00:00Z"},
            ]},
        },
        {
            "analysis": "Są świeże sygnały, ale sam ranking HN nie jest dowodem. Czytam wątek z praktyczną dyskusją.",
            "priority": "s1", "action": {"action": "read_thread", "item_id": "demo-thread-1"},
            "observation": {"kind": "read_thread", "thread": {"comments": [{"author": "maintainer", "text": "For small pull requests with clear tests, suggestions are useful. On large changes the noise becomes overwhelming."}]}, "evidence": [
                _demo_evidence("opinion", "For small pull requests with clear tests, suggestions are useful.", "s1"),
                _demo_evidence("sygnał ryzyka", "On large changes the noise becomes overwhelming.", "s1"),
            ]},
        },
        {
            "analysis": "Problemem nie jest katalog narzędzi. Wartość zależy od wielkości i jakości zmian, więc zmieniam pytanie badawcze.",
            "priority": "s3",
            "patch": {"reason": "Sygnał i szum są ważniejsze niż sama lista narzędzi.", "operations": [
                {"op": "drop", "step_id": "s3", "reason": "Katalog narzędzi nie odpowiada, czy pilot ograniczy szum."},
                {"op": "add", "question": "Dla jakich klas zmian AI code review zmniejsza szum zamiast go zwiększać?", "reason": "To ograniczenie wynika z obserwacji."},
            ]},
            "action": {"action": "fetch_source", "url": "https://news.ycombinator.com/"},
            "observation": {"kind": "fetch_source", "url": "https://news.ycombinator.com/", "summary": "Zapisany materiał pilota: sugestie pomagają przy małych, dobrze testowanych zmianach.", "evidence": [
                _demo_evidence("karta_dowodowa", "Przygotowana karta dowodowa: sugestie pomagają przy małych, dobrze testowanych zmianach.", "s2"),
            ]},
        },
        {
            "analysis": "Źródło potwierdza kierunek. Potrzebuję niezależnego ograniczenia, aby pilot nie był zbyt szeroki.",
            "priority": "s4", "action": {"action": "read_thread", "item_id": "demo-thread-2"},
            "observation": {"kind": "read_thread", "thread": {"comments": [{"author": "reviewer", "text": "False positives distracted reviewers and security-sensitive changes needed human review."}]}, "evidence": [
                _demo_evidence("counterargument", "False positives distracted reviewers and security-sensitive changes needed human review.", "s4"),
            ]},
        },
        {
            "analysis": "Mamy dowód i kontrargument. Dodaję miarę sukcesu; bez niej pilot byłby tylko kolejną opinią.",
            "priority": "s4",
            "patch": {"reason": "Pilot potrzebuje mierzalnego warunku sukcesu.", "operations": [
                {"op": "add", "question": "Jak zmierzymy, czy AI skraca review bez zwiększania fałszywych pozytywów?", "reason": "Kontrargument pokazał ryzyko szumu."},
            ]},
            "action": {"action": "ask_human", "question": "Co dla Was jest ważniejsze w pilocie: mniej szumu dla reviewerów czy wykrywanie większej liczby ryzyk?", "options": ["Mniej szumu dla reviewerów", "Wykrywanie większej liczby ryzyk"]},
            "observation": {"kind": "ask_human", "question": "Co dla Was jest ważniejsze w pilocie: mniej szumu dla reviewerów czy wykrywanie większej liczby ryzyk?", "options": ["Mniej szumu dla reviewerów", "Wykrywanie większej liczby ryzyk"]},
        },
        {
            "analysis": f"Człowiek wybrał: {chosen_metric}. To zmienia projekt pilota, a nie tylko opis raportu.",
            "priority": "s4",
            "patch": {"reason": "Odpowiedź człowieka wybiera miarę sukcesu pilota.", "operations": [
                {"op": "close", "step_id": "s1", "reason": "Sygnały zostały zebrane."},
                {"op": "close", "step_id": "s2", "reason": "Mamy kartę dowodową."},
                {"op": "close", "step_id": "s4", "reason": "Zakres eksperymentu został określony."},
                {"op": "close", "step_id": "s5", "reason": "Wiemy, dla jakich zmian zaczynamy."},
                {"op": "close", "step_id": "s6", "reason": "Miara sukcesu wynika z priorytetu człowieka."},
                {"op": "add", "question": f"Czy pilot dla {pilot_scope_for} poprawił wynik: {pilot_metric}?", "reason": "To pytanie sprawdzimy po zakończeniu pilota."},
            ]},
            "action": {"action": "fetch_source", "url": "https://news.ycombinator.com/"},
            "observation": {"kind": "fetch_source", "url": "https://news.ycombinator.com/", "summary": f"PILOT USTALONY: 30 dni; zakres: {pilot_scope}; zasada bezpieczeństwa: {pilot_guardrail}; miara sukcesu: {pilot_metric}."},
        },
        {
            "analysis": "Mamy podstawy do ograniczonego pilota, ale nie do wdrożenia globalnego. Kończę jasną rekomendacją i nazwą pytanie, na które pilot ma odpowiedzieć.",
            "priority": "s7", "action": {"action": "finish", "recommendation": "zbadaj", "reason": f"Uruchom 30-dniowy pilot dla {pilot_scope_for}. {pilot_guardrail.capitalize()}. Po 30 dniach oceń: {pilot_metric}. Nie wdrażaj rozwiązania globalnie przed tym pomiarem.", "unresolved_steps": ["s7"]},
            "observation": {"kind": "finish", "recommendation": "zbadaj", "reason": f"Uruchom 30-dniowy pilot dla {pilot_scope_for}. {pilot_guardrail.capitalize()}. Po 30 dniach oceń: {pilot_metric}. Nie wdrażaj rozwiązania globalnie przed tym pomiarem.", "unresolved_steps": ["s7"]},
        },
    ]


def demo_action_choices(expected_action: str) -> list[str]:
    """Małe menu pokazuje autonomię, ale nie daje modelowi dostępu do wszystkiego."""
    alternatives = {
        "search_hn": ["search_hn", "finish"],
        "read_thread": ["read_thread", "search_hn", "finish"],
        "fetch_source": ["fetch_source", "read_thread", "finish"],
        "ask_human": ["ask_human", "finish"],
        "finish": ["finish"],
    }
    return alternatives[expected_action]


DEMO_STAGE_GUIDANCE = [
    ("Zbierz sygnały, bo nie ma jeszcze żadnego materiału.", "Bez sygnału z rynku nie wolno kończyć researchu."),
    ("Odróżnij ranking od treści: przeczytaj rozmowę, zanim wyciągniesz wniosek.", "Wynik wyszukiwania jest sygnałem. Najpierw trzeba przeczytać materiał."),
    ("Sprawdź, czy obserwacja o szumie ma oparcie w przygotowanej karcie dowodowej.", "Przed zmianą planu potrzebna jest karta dowodowa, nie sama opinia."),
    ("Zbierz niezależny kontrargument o fałszywych pozytywach i zmianach security.", "Jedna karta dowodowa nie wystarcza: potrzebny jest niezależny kontrargument przed projektem pilota."),
    ("Ustal z człowiekiem, czy pilot ma zmniejszać szum czy wykrywać więcej ryzyk.", "Tego priorytetu nie ma w danych. Bez decyzji człowieka nie wolno projektować pilota."),
    ("Przełóż wybrany przez człowieka priorytet na zakres, bezpiecznik i miarę sukcesu pilota.", "Raport nie wystarczy: przed zakończeniem musi powstać konkretny, mierzalny pilot."),
    ("Zakończ tylko ograniczoną rekomendacją i nazwij pytanie, na które odpowie pilot.", "Można zakończyć dopiero po zdefiniowaniu pilota i jego miary sukcesu."),
]


def decide_demo_action(state: RobotState, expected_action: str) -> tuple[DemoApiDecision, list[str], list[dict[str, str]]]:
    choices = demo_action_choices(expected_action)
    stage_goal, policy_reason = DEMO_STAGE_GUIDANCE[state.demo_index]
    prompt = f"""Jesteś robotem badawczym w kontrolowanym scenariuszu szkoleniowym.
Na podstawie stanu wybierz następną akcję wyłącznie z listy: {choices}.
Wybierz akcję, która najbardziej przybliża do decyzji; `finish` wybieraj tylko,
jeśli raport jest już uzasadniony. Uzasadnienie ma być krótkie, konkretne i odwoływać
się do obserwacji lub luki w dowodach. Nie używaj ogólników typu "potrzebujemy więcej
informacji". Nie opisuj toku rozumowania krok po kroku.

CEL TEJ TURY: {stage_goal}

AKTUALNY STAN:
{_view(state)}"""
    decision = call_api(
        [{"role": "system", "content": "Wybierasz jedną akcję w schemacie JSON i podajesz audytowalne uzasadnienie."}, {"role": "user", "content": prompt}],
        DemoApiDecision,
    )
    if decision.selected_action not in choices:
        raise ValueError(f"Model wybrał akcję poza dozwolonym menu: {decision.selected_action}.")
    if decision.selected_action == expected_action:
        return decision, choices, []

    # Model może zaproponować poprawną składniowo, ale złą biznesowo akcję.
    # Kod nie wykonuje jej w ciemno: zapisuje odrzucenie i prosi o replan.
    rejected = {"selected_action": decision.selected_action, "rationale": decision.rationale, "reason": policy_reason, "choices": " | ".join(choices)}
    retry_prompt = f"""Poprzednia propozycja `{decision.selected_action}` została odrzucona przez politykę:
{policy_reason}
Wybierz teraz jedyną akcję, która przechodzi tę bramkę: `{expected_action}`.
Podaj nowe, krótkie uzasadnienie oparte na stanie:
{_view(state)}"""
    retry = call_api(
        [{"role": "system", "content": "Zwracasz JSON z wybraną akcją i krótkim uzasadnieniem."}, {"role": "user", "content": retry_prompt}],
        DemoApiDecision,
    )
    if retry.selected_action != expected_action:
        raise ValueError(f"Model nie zastosował się do bramki polityki: {retry.selected_action}.")
    return retry, [expected_action], [rejected]


def run_demo_turn(state: RobotState, answer: str | None = None):
    turns = _demo_turns(answer)
    if state.demo_index >= len(turns):
        return None, {"kind": "finish"}, []
    item = turns[state.demo_index]
    api_decision, choices, rejected_proposals = decide_demo_action(state, item["action"]["action"])
    step = NextStep(situation_analysis=item["analysis"], current_priority=item["priority"], plan_patch=PlanPatch.model_validate(item["patch"]) if item.get("patch") else None, next_action=item["action"])
    view = _view(state)
    changes = apply_patch(state, step.plan_patch)
    # Każda tura dostaje świeżą kopię: ponowne uruchomienie demonstracji
    # nie może utracić dowodów przez wcześniejsze `pop`.
    observation = {**item["observation"], "demo": True}
    state.evidence.extend(observation.pop("evidence", []))
    if observation["kind"] in {"read_thread", "fetch_source"}:
        state.material_reads += 1
    if observation["kind"] == "ask_human": state.pending_question = step.next_action
    state.observations.append(observation)
    state.history.append({"state_view": view, "next_step": step.model_dump(), "observation": observation, "changes": changes, "allowed_actions": allowed_actions(state), "api_decision": api_decision.model_dump(), "api_choices": choices, "rejected_proposals": rejected_proposals})
    state.demo_index += 1
    return step, observation, changes


def unread_threads_with_comments(state):
    latest_results = next((x.get("results", []) for x in reversed(state.observations) if x.get("kind") == "search_hn"), [])
    return [row for row in latest_results if row["id"] not in state.seen_item_ids and row.get("comments", 0) > 0]


def eligible_source_urls(state):
    urls = []
    for observation in state.observations:
        for row in observation.get("results", []) if observation.get("kind") == "search_hn" else []:
            # Model nie może wymyślić adresu. Może otworzyć wyłącznie link,
            # który wcześniej pokazał HN i przeszedł filtr trafności.
            if row["url"] not in urls:
                urls.append(row["url"])
    return urls


def allowed_actions(state):
    if len(state.history) >= MAX_TURNS - 1 or state.consecutive_rejections >= 2: return ["finish"]
    # To jest bramka, a nie sugestia dla modelu. Jeżeli wyszukiwarka znalazła
    # dyskusję z komentarzami, robot najpierw musi ją przeczytać.
    if not state.evidence and unread_threads_with_comments(state):
        return ["read_thread"]
    # Gdy trafny wynik prowadzi do tekstu źródłowego, najpierw czytamy go.
    # Kolejne wyszukiwanie bez przeczytania czegokolwiek niczego nie wnosi.
    if not state.evidence and eligible_source_urls(state):
        return ["fetch_source"]
    actions = ["search_hn", "read_thread", "fetch_source", "ask_human", "finish"]
    if not eligible_source_urls(state):
        actions.remove("fetch_source")
    if state.consecutive_searches >= MAX_CONSECUTIVE_SEARCHES: actions.remove("search_hn")
    # Po udanym wyszukaniu robot nie może udawać, że kolejny search jest
    # researchiem. Najpierw ma przeczytać choć jeden znaleziony materiał.
    # Nie eskalujemy pustego researchu do człowieka. Najpierw robot ma sam
    # przeczytać dostępny materiał i dopiero wtedy może sensownie pokazać wybór.
    if not state.evidence: actions.remove("ask_human")
    # Człowiek odpowiedział już na pytanie eskalacyjne. Teraz robot ma użyć
    # tej informacji, a nie pytać w kółko o to samo pod innymi słowami.
    if state.human_context: actions.remove("ask_human")
    return actions


def decision_model_for(actions):
    """Schema odpowiedzi jest dodatkową bramką, nie tylko tekstem w prompcie."""
    types = {"search_hn": SearchHn, "read_thread": ReadThread, "fetch_source": FetchSource, "ask_human": AskHuman, "finish": Finish}
    choices = tuple(types[name] for name in actions)
    return create_model("NextStepForTurn", situation_analysis=(str, ...), current_priority=(str, ...), plan_patch=(PlanPatch | None, ...), next_action=(Union[choices], ...))


def evidence_counts(state):
    return {"primary": sum(x["kind"] == "primary_source" for x in state.evidence), "counterargument": sum(x["kind"] == "counterargument" for x in state.evidence), "signals": len(state.evidence)}


def policy_alerts(state):
    alerts = []
    if not state.evidence: alerts.append("Nie masz jeszcze dowodu. Przeczytaj wątek albo źródło, nie tylko szukaj.")
    if state.consecutive_searches >= MAX_CONSECUTIVE_SEARCHES: alerts.append("search_hn jest zablokowane do czasu przeczytania materiału.")
    if state.consecutive_rejections >= 2: alerts.append("Dwie decyzje z rzędu odrzucone. Możesz już tylko uczciwie zakończyć zadanie.")
    return alerts


def _clean(value): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()
def _stale(value, horizon): return datetime.fromisoformat(value.replace("Z", "+00:00")).date() < date.today() - timedelta(days=horizon)
def _source_kind(url): return SOURCE_POLICY.get(urlparse(url).netloc.lower())


def _query_terms(query):
    ignored = {"and", "the", "for", "with", "from", "that", "this", "about", "czy", "warto", "zrobić", "spike"}
    return {word for word in re.findall(r"[a-zA-Z]{3,}", query.lower()) if word not in ignored}


def _is_relevant_to_query(row, query):
    terms = _query_terms(query)
    text = f"{row['title']} {row['url']}".lower()
    # Jeden przypadkowy wyraz to za mało. Dla pytania o code review wymagamy
    # co najmniej dwóch istotnych słów, np. "code" oraz "review".
    return len([term for term in terms if term in text]) >= min(2, len(terms))


def search_hn(query):
    response = requests.get("https://hn.algolia.com/api/v1/search_by_date", params={"query": query, "tags": "story", "hitsPerPage": 7}, timeout=20)
    response.raise_for_status()
    rows = [{"id": h["objectID"], "title": h.get("title") or "(bez tytułu)", "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}", "points": h.get("points", 0), "comments": h.get("num_comments", 0), "created_at": h["created_at"]} for h in response.json()["hits"]]
    return [row for row in rows if _is_relevant_to_query(row, query)]


def read_thread(item_id):
    response = requests.get(f"https://hn.algolia.com/api/v1/items/{item_id}", timeout=20)
    response.raise_for_status(); item = response.json()
    return {"id": item_id, "comments": [{"author": c.get("author", "anon"), "text": _clean(c.get("text", ""))[:700]} for c in item.get("children", [])[:7] if c.get("text")]}


def apply_patch(state, patch):
    if patch is None: return []
    plan, changes = [x.model_copy() for x in state.plan], []
    known = {re.sub(r"[^\w\s]", "", x.question.lower()).strip() for x in plan}
    for op in patch.operations:
        if isinstance(op, AddStep):
            normalized = re.sub(r"[^\w\s]", "", op.question.lower()).strip()
            if normalized in known: raise ValueError("Patch odrzucony: nowe pytanie dubluje istniejący krok.")
            step_id = f"s{len(plan) + 1}"; plan.append(PlanStep(id=step_id, question=op.question, reason=op.reason)); known.add(normalized); changes.append(f"+ {step_id}: {op.question}")
        else:
            step = next((x for x in plan if x.id == op.step_id), None)
            if step is None or step.status != "open": raise ValueError(f"Patch odrzucony: krok {op.step_id} nie jest otwarty.")
            step.status = "closed" if isinstance(op, CloseStep) else "dropped"; step.reason = op.reason; changes.append(f"{step.status}: {step.id}")
    state.plan = plan; return changes


def _view(state):
    plan = "\n".join(f"- {x.id} [{x.status}] {x.question}" for x in state.plan)
    evidence = "\n".join(f"- [{x['kind']}] {x['claim']}" for x in state.evidence[-7:]) or "- brak"
    last = state.observations[-1] if state.observations else {"kind": "start", "summary": "Jeszcze nic nie sprawdziłeś."}
    readable_threads = ""
    unread_threads = unread_threads_with_comments(state)
    if unread_threads and not state.evidence:
        readable_threads = "\nWĄTKI DO PRZECZYTANIA: " + " | ".join(f"{x['id']}: {x['title']}" for x in unread_threads)
    source_urls = eligible_source_urls(state)
    readable_sources = "\nDOZWOLONE ŹRÓDŁA: " + " | ".join(source_urls) if source_urls else ""
    counts = evidence_counts(state)
    completion_rule = (
        f"scenariusz wymaga karta_dowodowa=1 i counterargument=1; masz karta_dowodowa={sum(x['kind'] == 'karta_dowodowa' for x in state.evidence)}, counterargument={counts['counterargument']}."
        if state.mode == "demo"
        else f"działaj wymaga primary=1 i counterargument=1; masz primary={counts['primary']}, counterargument={counts['counterargument']}."
    )
    return f"""KONTRAKT: {state.question}; horyzont {state.horizon_days} dni; standard: {state.contract.evidence_standard.value}
PLAN - pytania badawcze:\n{plan}
DOWODY:\n{evidence}
OSTATNIA OBSERWACJA: {last}
KONTEKST CZŁOWIEKA: {state.human_context or '- brak'}
ABY ZAKOŃCZYĆ: {completion_rule}
DOZWOLONE AKCJE: {' | '.join(allowed_actions(state))}
{readable_threads}
{readable_sources}
Wybierz jedną akcję. HN jest sygnałem, nie dowodem. Plan zmieniaj tylko z konkretnym powodem."""


SYSTEM_PROMPT = """Jesteś Mój Robot, samodzielnym analitykiem technologicznym.
Plan to hipoteza, nie lista czynności. Po obserwacji wybierz jedną dozwoloną akcję;
nie kończ po samym wyszukaniu. Pytanie do człowieka jest eskalacją, nie strategią:
zadaj je tylko wtedy, gdy bez tej odpowiedzi nie możesz wybrać drogi. Gdy człowiek
już odpowiedział, wykorzystaj jego odpowiedź i nie pytaj ponownie o ten sam kontekst.
Zanim zapytasz człowieka, najpierw przeczytaj dostępny wątek albo źródło.
Jeśli brakuje danych, nie rekomenduj "odpuść". Zakończ jako "brak podstaw" i nazwij lukę."""


def decide_next_step(state):
    view = _view(state); schema = FinishOnly if allowed_actions(state) == ["finish"] else decision_model_for(allowed_actions(state))
    return call_api([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": view}], schema), view


def dispatch(state, step):
    action = step.next_action
    if action.action not in allowed_actions(state): return {"kind": "rejected", "rejected": True, "reason": f"Akcja {action.action} nie jest dozwolona w tej turze."}
    if isinstance(action, SearchHn):
        results = [] if action.query in state.seen_queries else search_hn(action.query); state.seen_queries.append(action.query); state.consecutive_searches += 1
        for row in results: row["stale"] = _stale(row["created_at"], state.horizon_days)
        return {"kind": "search_hn", "query": action.query, "results": results[:3], "alert": "Brak nowych wyników." if not results else ""}
    if isinstance(action, ReadThread):
        if action.item_id in state.seen_item_ids: return {"kind": "read_thread", "alert": "Ten wątek był już przeczytany."}
        latest_results = next((x.get("results", []) for x in reversed(state.observations) if x.get("kind") == "search_hn"), [])
        if latest_results and action.item_id not in {row["id"] for row in latest_results}:
            return {"kind": "rejected", "rejected": True, "reason": "Robot może czytać tylko wątek znaleziony w poprzedniej turze."}
        try:
            thread = read_thread(action.item_id)
        except requests.RequestException as error:
            return {"kind": "read_thread", "item_id": action.item_id, "error": f"Nie udało się przeczytać wątku: {error.__class__.__name__}."}
        state.seen_item_ids.append(action.item_id); state.consecutive_searches = 0; state.material_reads += 1
        for comment in thread["comments"][:2]: state.evidence.append({"kind": "opinion", "claim": comment["text"], "quote": comment["text"], "source_url": f"https://news.ycombinator.com/item?id={action.item_id}", "supports_step": step.current_priority})
        return {"kind": "read_thread", "item_id": action.item_id, "thread": thread, "evidence_added": min(2, len(thread["comments"]))}
    if isinstance(action, FetchSource):
        if action.url not in eligible_source_urls(state):
            return {"kind": "rejected", "rejected": True, "reason": "Robot może pobrać wyłącznie URL pokazany w polu DOZWOLONE ŹRÓDŁA."}
        kind = _source_kind(action.url) or "case_claim"
        try:
            text = _clean(requests.get(action.url, timeout=15, headers={"User-Agent": "DWthonAdaptive/1.0"}).text)[:350]
            state.evidence.append({"kind": kind, "claim": text, "quote": text, "source_url": action.url, "supports_step": "s2"}); state.consecutive_searches = 0; state.material_reads += 1
            return {"kind": "fetch_source", "url": action.url, "summary": text, "evidence_added": 1}
        except Exception as error: return {"kind": "fetch_source", "url": action.url, "error": str(error)}
    if isinstance(action, AskHuman): state.pending_question = action; return {"kind": "ask_human", "question": action.question, "options": action.options}
    counts = evidence_counts(state)
    if action.recommendation == "działaj" and (counts["primary"] < 1 or counts["counterargument"] < 1): return {"kind": "rejected", "rejected": True, "reason": "Rekomendacja 'działaj' zablokowana: brakuje źródła pierwotnego lub kontrargumentu."}
    if action.recommendation == "odpuść" and counts["signals"] == 0:
        return {"kind": "finish", "recommendation": "brak podstaw", "reason": "Robot nie zebrał wystarczających danych, aby uczciwie rekomendować rezygnację.", "unresolved_steps": action.unresolved_steps}
    return {"kind": "finish", "recommendation": action.recommendation, "reason": action.reason, "unresolved_steps": action.unresolved_steps}


def commit_turn(state, step, view):
    try: changes = apply_patch(state, step.plan_patch); observation = dispatch(state, step)
    except ValueError as error: changes, observation = [], {"kind": "rejected", "rejected": True, "reason": str(error)}
    except Exception as error: changes, observation = [], {"kind": "tool_error", "error": f"Nie udało się wykonać akcji: {error.__class__.__name__}."}
    state.consecutive_rejections = state.consecutive_rejections + 1 if observation.get("rejected") else 0
    state.observations.append(observation); state.history.append({"state_view": view, "next_step": step.model_dump(), "observation": observation, "changes": changes, "allowed_actions": allowed_actions(state)})
    return observation, changes


def answer_human(state, answer):
    state.human_context.append(answer); state.pending_question = None; state.observations.append({"kind": "human_answer", "answer": answer})
