from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal, Union

import instructor
import requests
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, create_model


load_dotenv()
client = instructor.patch(
    OpenAI(base_url="https://llm-api.dataworkshop.eu"), mode=instructor.Mode.MD_JSON
)
MAX_TURNS = 7
MAX_CONSECUTIVE_SEARCHES = 2


def track(event: str, **properties: Any) -> None:
    """Punkt na analitykę warsztatu; domyślnie celowo nic nie wypisuje."""


def call_api(messages: list[dict[str, str]], response_model: type):
    return client.chat.completions.create(
        model="gpt-4o-mini", messages=messages, response_model=response_model,
        temperature=0.0, max_retries=2,
    )


class PlanStep(BaseModel):
    id: str
    question: str
    status: Literal["open", "closed", "dropped"] = "open"
    reason: str | None = None


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


PatchOperation = Union[AddStep, DropStep, CloseStep]


class PlanPatch(BaseModel):
    reason: str
    operations: list[PatchOperation]


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


Action = Union[SearchHn, ReadThread, FetchSource, AskHuman, Finish]


class NextStep(BaseModel):
    situation_analysis: str = Field(description="Co wynika z ostatniej obserwacji?")
    current_priority: str = Field(description="ID najważniejszego otwartego pytania")
    plan_patch: PlanPatch | None = Field(description="Zmiana planu albo null")
    next_action: Action


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
    evidence: list[dict[str, str]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    consecutive_searches: int = 0
    seen_queries: list[str] = field(default_factory=list)
    seen_item_ids: list[str] = field(default_factory=list)


def initial_state(question: str, horizon_days: int = 90) -> RobotState:
    contract = ResearchContract(
        ContractField(question, "user"),
        ContractField(horizon_days, "user"),
        ContractField("co najmniej 1 źródło pierwotne i 1 kontrargument", "default"),
        ContractField(["AI code review", "LLM code review"], "inferred"),
        ContractField("zapytaj tylko, gdy odpowiedź zmieni dalszą decyzję", "default"),
    )
    return RobotState(contract, [
        PlanStep(id="s1", question="Jakie świeże sygnały i problemy zgłasza społeczność?"),
        PlanStep(id="s2", question="Czy poza opiniami istnieje dowód realnego zastosowania?"),
        PlanStep(id="s3", question="Jaki mały eksperyment najbardziej zmniejszy ryzyko decyzji?"),
    ])


def search_hn(query: str, limit: int = 7) -> list[dict[str, Any]]:
    response = requests.get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"query": query, "tags": "story", "hitsPerPage": limit}, timeout=20,
    )
    response.raise_for_status()
    return [{
        "id": hit["objectID"], "title": hit.get("title") or "(bez tytułu)",
        "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
        "points": hit.get("points", 0), "comments": hit.get("num_comments", 0),
        "created_at": hit["created_at"],
    } for hit in response.json()["hits"]]


def read_thread(item_id: str) -> dict[str, Any]:
    response = requests.get(f"https://hn.algolia.com/api/v1/items/{item_id}", timeout=20)
    response.raise_for_status()
    item = response.json()
    comments = [child.get("text", "").replace("<p>", " ").replace("</p>", " ")
                for child in item.get("children", [])[:5] if child.get("text")]
    return {"id": item_id, "comments": comments}


def allowed_actions(state: RobotState) -> list[str]:
    if len(state.history) >= MAX_TURNS - 1:
        return ["finish"]
    actions = ["search_hn", "read_thread", "fetch_source", "ask_human", "finish"]
    if state.consecutive_searches >= MAX_CONSECUTIVE_SEARCHES:
        actions.remove("search_hn")
    return actions


def decision_model_for(actions: list[str]):
    """Warsztatowy przykład: niedozwolona akcja znika także ze schematu LLM."""
    action_types = {
        "search_hn": SearchHn, "read_thread": ReadThread, "fetch_source": FetchSource,
        "ask_human": AskHuman, "finish": Finish,
    }
    choices = tuple(action_types[name] for name in actions)
    return create_model(
        "NextStepForThisTurn",
        situation_analysis=(str, Field(description="Co wynika z obserwacji?")),
        current_priority=(str, Field(description="Najważniejsze ID planu")),
        plan_patch=(PlanPatch | None, Field(description="Patch albo null")),
        next_action=(Union[choices], Field(description="Akcja dozwolona teraz")),
    )


def apply_patch(state: RobotState, patch: PlanPatch | None) -> list[str]:
    if patch is None:
        return []
    plan = [step.model_copy() for step in state.plan]
    changes: list[str] = []
    for operation in patch.operations:
        if isinstance(operation, AddStep):
            step_id = f"s{len(plan) + 1}"
            plan.append(PlanStep(id=step_id, question=operation.question, reason=operation.reason))
            changes.append(f"+ {step_id}: {operation.question}")
            continue
        step = next((item for item in plan if item.id == operation.step_id), None)
        if step is None or step.status != "open":
            raise ValueError(f"Patch odrzucony: krok {operation.step_id} nie jest otwarty.")
        step.status = "closed" if isinstance(operation, CloseStep) else "dropped"
        step.reason = operation.reason
        changes.append(f"{step.status}: {step.id}")
    state.plan = plan
    return changes


def _stale(created_at: str, horizon_days: int) -> bool:
    published = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
    return published < date.today() - timedelta(days=horizon_days)


def render_state_for_llm(state: RobotState) -> str:
    contract = state.contract
    plan = "\n".join(f"- {s.id} [{s.status}] {s.question}" for s in state.plan)
    evidence = "\n".join(f"- [{e['kind']}] {e['quote']}" for e in state.evidence[-5:]) or "- brak"
    observation = state.observations[-1] if state.observations else {"kind": "start", "summary": "Jeszcze nic nie sprawdziłeś."}
    return f"""KONTRAKT
CEL: {contract.decision.value} [{contract.decision.source}]
HORYZONT: {contract.horizon_days.value} dni [{contract.horizon_days.source}]
STANDARD: {contract.evidence_standard.value}
PLAN (pytania, nie lista czynności):
{plan}
DOWODY:\n{evidence}
OSTATNIA OBSERWACJA: {observation}
DOZWOLONE AKCJE: {' | '.join(allowed_actions(state))}
BUDŻET: tura {len(state.history) + 1}/{MAX_TURNS}
Wybierz jedną akcję. HN jest sygnałem, nie dowodem. Patch musi wynikać z obserwacji."""


def dispatch(state: RobotState, step: NextStep) -> dict[str, Any]:
    action = step.next_action
    if action.action not in allowed_actions(state):
        return {"kind": "rejected", "rejected": True, "reason": f"Akcja {action.action} nie jest dozwolona."}
    if isinstance(action, SearchHn):
        results = [] if action.query in state.seen_queries else search_hn(action.query)
        state.seen_queries.append(action.query)
        state.consecutive_searches += 1
        for item in results:
            item["stale"] = _stale(item["created_at"], state.contract.horizon_days.value)
        return {"kind": "search_hn", "query": action.query, "results": results[:3]}
    if isinstance(action, ReadThread):
        if action.item_id in state.seen_item_ids:
            return {"kind": "read_thread", "alert": "Ten wątek był już przeczytany."}
        thread = read_thread(action.item_id)
        state.seen_item_ids.append(action.item_id)
        state.consecutive_searches = 0
        if thread["comments"]:
            quote = thread["comments"][0][:350]
            state.evidence.append({"kind": "opinion", "quote": quote, "source_url": f"https://news.ycombinator.com/item?id={action.item_id}"})
        return {"kind": "read_thread", "item_id": action.item_id, "comments": len(thread["comments"])}
    if isinstance(action, FetchSource):
        state.consecutive_searches = 0
        return {"kind": "fetch_source", "url": action.url, "alert": "W Day 2 źródło jest miejscem na walidowany dowód."}
    if isinstance(action, AskHuman):
        return {"kind": "ask_human", "question": action.question, "options": action.options}
    return {"kind": "finish", "recommendation": action.recommendation, "reason": action.reason,
            "unresolved_steps": action.unresolved_steps}


SYSTEM_PROMPT = """Jesteś robotem badawczym. Po każdej obserwacji wybierasz jedną akcję.
Plan zawiera pytania, nie listę kroków. Możesz dodać, zamknąć albo porzucić pytanie
tylko z powodem wynikającym z obserwacji. Nie kończ po samym wyszukaniu."""


def commit_turn(state: RobotState, step: NextStep, view: str) -> tuple[dict[str, Any], list[str]]:
    try:
        changes = apply_patch(state, step.plan_patch)
        observation = dispatch(state, step)
    except ValueError as error:
        changes, observation = [], {"kind": "rejected", "rejected": True, "reason": str(error)}
    state.observations.append(observation)
    state.history.append({"state_view": view, "next_step": step.model_dump(), "observation": observation,
                          "changes": changes, "allowed_actions": allowed_actions(state)})
    return observation, changes


def run_turn(state: RobotState) -> tuple[NextStep, dict[str, Any], list[str]]:
    view = render_state_for_llm(state)
    step = call_api([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": view}], NextStep)
    observation, changes = commit_turn(state, step, view)
    return step, observation, changes
