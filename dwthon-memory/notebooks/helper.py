from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Dict, List, Optional

from jinja2 import Template
from openai import OpenAI
from pydantic import BaseModel, Field
import instructor

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - fallback for runtimes bez python-dotenv
    def load_dotenv():
        return False


load_dotenv()


def make_client():
    base = OpenAI(base_url="https://llm-api.dataworkshop.eu")
    try:
        return instructor.from_openai(base, mode=instructor.Mode.MD_JSON)
    except AttributeError:
        return instructor.patch(base, mode=instructor.Mode.MD_JSON)


client = make_client()


class CoreMemory(BaseModel):
    name: str = "Coach"
    creator_name: str = "DataWorkshop"
    role: str = "Language Communication Coach"
    prime_directives: List[str] = [
        "NO_HOMEWORK_SOLVING: nigdy nie podawaj gotowców - naprowadzaj",
        "COMMUNICATION_FIRST: priorytetem jest płynność, nie idealna gramatyka",
        "CONTINUITY: jeśli pamięć daje kontekst, nie zaczynaj sesji od zera",
    ]


class LearnerGoal(BaseModel):
    goal: str
    reason: Optional[str] = None
    target_situation: Optional[str] = None
    success_metric: Optional[str] = None
    deadline: Optional[str] = None


class WorkContext(BaseModel):
    profession: Optional[str] = None
    company_name: Optional[str] = None
    industry: Optional[str] = None
    high_stakes_situations: List[str] = Field(default_factory=list)
    stakeholders: List[str] = Field(default_factory=list)


class CommunicationPreferences(BaseModel):
    feedback_style: Optional[str] = None
    correction_intensity: Optional[str] = None
    pace: Optional[str] = None
    preferred_formats: List[str] = Field(default_factory=list)


class PersonalContext(BaseModel):
    personal_facts: List[str] = Field(default_factory=list)
    relationship_hooks: List[str] = Field(default_factory=list)
    upcoming_events: List[str] = Field(default_factory=list)
    schedule_constraints: List[str] = Field(default_factory=list)


class SemanticSignals(BaseModel):
    confidence_blockers: List[str] = Field(default_factory=list)
    motivation_drivers: List[str] = Field(default_factory=list)
    recurring_topics: List[str] = Field(default_factory=list)
    vocabulary_targets: List[str] = Field(default_factory=list)


class PerformanceProfile(BaseModel):
    target_scenarios: List[str] = Field(default_factory=list)
    tone_targets: List[str] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    recurring_weaknesses: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)


class StudentProfile(BaseModel):
    user_id: str
    name: str
    level: str = Field(description="Poziom, np. B1")
    goal: str = Field(description="Po co się uczy")
    native_language: str = "Polish"
    target_language: str = "English"
    preferences: List[str] = Field(default_factory=list)
    goals: List[LearnerGoal] = Field(default_factory=list)
    work_context: WorkContext = Field(default_factory=WorkContext)
    communication_preferences: CommunicationPreferences = Field(default_factory=CommunicationPreferences)
    personal_context: PersonalContext = Field(default_factory=PersonalContext)
    semantic_signals: SemanticSignals = Field(default_factory=SemanticSignals)
    performance_profile: PerformanceProfile = Field(default_factory=PerformanceProfile)
    context_notes: Optional[str] = None


class LearningProgress(BaseModel):
    recent_mistakes: List[str] = Field(default_factory=list)
    homework_status: str = "none"
    next_focus: Optional[str] = None


class SessionPhase(str, Enum):
    HOMEWORK_CHECK = "HOMEWORK_CHECK"
    PRACTICE = "PRACTICE"
    WRAP_UP = "WRAP_UP"


class WorkflowMemory(BaseModel):
    phase: SessionPhase = SessionPhase.HOMEWORK_CHECK
    topic: str


class WorkingMemory(BaseModel):
    history: List[Dict[str, str]] = Field(default_factory=list)

    def add(self, role: str, content: str):
        self.history.append({"role": role, "content": content})

    def get_context_window(self, limit: int = 6) -> List[Dict[str, str]]:
        return self.history[-limit:]


class MistakeLog(BaseModel):
    user_phrase: str
    correction: str
    rule: str


class Episode(BaseModel):
    summary: str = Field(description="Jednozdaniowe podsumowanie sesji")
    personal_facts: List[str] = Field(default_factory=list)
    mistakes: List[MistakeLog] = Field(default_factory=list)
    next_step: str = Field(default="", description="Co ćwiczyć dalej")


class EpisodicMemory(BaseModel):
    episodes: List[Episode] = Field(default_factory=list)
    max_episodes: int = 10

    def add(self, episode: Episode):
        self.episodes.append(episode)
        self.episodes = self.episodes[-self.max_episodes :]

    def recent(self, limit: int = 2) -> List[Episode]:
        return self.episodes[-limit:]


class AgentState(BaseModel):
    core: CoreMemory
    profile: StudentProfile
    progress: LearningProgress
    workflow: WorkflowMemory
    working: WorkingMemory
    episodic: EpisodicMemory


class TutorReply(BaseModel):
    reply: str = Field(description="Odpowiedź tutora do ucznia")


class SemanticWriteCandidate(BaseModel):
    personal_facts: List[str] = Field(default_factory=list)
    relationship_hooks: List[str] = Field(default_factory=list)
    upcoming_events: List[str] = Field(default_factory=list)
    work_situations: List[str] = Field(default_factory=list)
    preferences: List[str] = Field(default_factory=list)
    confidence_blockers: List[str] = Field(default_factory=list)
    motivation_drivers: List[str] = Field(default_factory=list)
    vocabulary_targets: List[str] = Field(default_factory=list)
    tone_targets: List[str] = Field(default_factory=list)
    recurring_weaknesses: List[str] = Field(default_factory=list)


class LiveTutorReply(BaseModel):
    reply: str = Field(description="Odpowiedź tutora do ucznia")
    semantic_write: SemanticWriteCandidate = Field(
        default_factory=SemanticWriteCandidate,
        description=(
            "Strukturalny pakiet nowych informacji do pamięci semantycznej. "
            "Zapisuj tylko rzeczy trwałe albo operacyjnie ważne dla kolejnych sesji."
        ),
    )
    core_guardrail_used: bool = Field(
        default=False,
        description=(
            "Ustaw na true, jeśli odpowiedź aktywnie zastosowała twardą zasadę Core Memory, "
            "np. odmówiła zrobienia pracy domowej za ucznia."
        ),
    )
    core_guardrail_reason: Optional[str] = Field(
        default=None,
        description="Krótki powód, jaka zasada core została użyta albo zablokowała odpowiedź.",
    )


PROMPT_TEMPLATE = Template(
    """
Jesteś {{ s.core.name }} - {{ s.core.role }} stworzonym przez {{ s.core.creator_name }}.

ZASADY:
{% for rule in s.core.prime_directives -%}
- {{ rule }}
{% endfor %}

UCZEŃ:
- Imię: {{ s.profile.name }}
- Poziom: {{ s.profile.level }}
- Język: {{ s.profile.native_language }} -> {{ s.profile.target_language }}
- Cel główny: {{ s.profile.goal }}
- Preferencje: {{ s.profile.preferences | join(", ") or "brak" }}
- Rola zawodowa: {{ s.profile.work_context.profession or "brak" }}{% if s.profile.work_context.company_name %} @ {{ s.profile.work_context.company_name }}{% endif %}{% if s.profile.work_context.industry %} | branża: {{ s.profile.work_context.industry }}{% endif %}
- Sytuacje wysokiej stawki: {{ s.profile.work_context.high_stakes_situations | join("; ") or "brak" }}
- Nadchodzące wydarzenia: {{ s.profile.personal_context.upcoming_events | join("; ") or "brak" }}
- Haki relacyjne: {{ s.profile.personal_context.relationship_hooks | join("; ") or "brak" }}
- Blokery pewności: {{ s.profile.semantic_signals.confidence_blockers | join("; ") or "brak" }}
- Motywatory: {{ s.profile.semantic_signals.motivation_drivers | join("; ") or "brak" }}
- Słowa / obszary do złapania: {{ s.profile.semantic_signals.vocabulary_targets | join("; ") or "brak" }}
- Docelowy ton / vibe: {{ s.profile.performance_profile.tone_targets | join("; ") or "brak" }}
- Powtarzające się słabości: {{ s.profile.performance_profile.recurring_weaknesses | join("; ") or "brak" }}
- Kryteria sukcesu: {{ s.profile.performance_profile.success_criteria | join("; ") or "brak" }}
- Semantic summary: {{ s.profile.context_notes or "brak" }}

POSTĘP:
- Ostatnie błędy: {{ s.progress.recent_mistakes | join("; ") or "brak" }}
- Następny fokus: {{ s.progress.next_focus or "brak" }}
- Status pracy domowej: {{ s.progress.homework_status }}

{% if s.episodic.episodes %}
OSTATNIE EPIZODY:
{% for e in s.episodic.recent(2) -%}
- {{ e.summary }} | następny krok: {{ e.next_step or "brak" }}
{% endfor %}
{% endif %}

INSTRUKCJE CIĄGŁOŚCI:
- Jeśli pamiętasz wcześniejsze błędy lub następny fokus, nawiąż do nich naturalnie na początku nowej sesji.
- Jeśli w semantyce jest coś osobistego lub operacyjnie ważnego, możesz użyć tego do przełamania lodów.
- Nie zalewaj ucznia teorią. Prowadź krótko, praktycznie i po ludzku.

AKTUALNA FAZA: {{ s.workflow.phase.value }}
{% if s.workflow.phase.value == "HOMEWORK_CHECK" -%}
👉 Sprawdź pracę domową. NIE zaczynaj nowego roleplayu i NIE zdradzaj jeszcze tematu.
{% elif s.workflow.phase.value == "PRACTICE" -%}
👉 Poprowadź ćwiczenie / roleplay na temat: "{{ s.workflow.topic }}". Poprawiaj tylko błędy krytyczne lub powtarzające się.
{% elif s.workflow.phase.value == "WRAP_UP" -%}
👉 Podsumuj, nazwij 1-2 rzeczy do poprawy i zadaj krótką pracę domową.
{% endif %}
"""
)


def build_prompt(state: AgentState) -> str:
    return PROMPT_TEMPLATE.render(s=state).strip()


def run_turn(state: AgentState, user_message: str, model: str = "gpt-4o-mini") -> str:
    state.working.add("user", user_message)
    system_prompt = build_prompt(state)
    messages = [{"role": "system", "content": system_prompt}] + state.working.get_context_window()

    result = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        response_model=TutorReply,
        max_retries=2,
    )
    state.working.add("assistant", result.reply)
    return result.reply


def run_turn_live(
    state: AgentState,
    user_message: str,
    model: str = "gpt-4o-mini",
) -> LiveTutorReply:
    state.working.add("user", user_message)
    system_prompt = build_prompt(state) + """

INSTRUKCJA DLA STRUKTURY WYJŚCIA:
- `semantic_write`: zapisuj tylko nowe, trwałe lub operacyjnie ważne informacje.
- `personal_facts`: dzieci, zwierzęta, miasto, stałe realia życiowe.
- `relationship_hooks`: drobne rzeczy, od których można naturalnie zacząć rozmowę.
- `upcoming_events`: prezentacje, rozmowy, demo, terminy, spotkania wysokiej stawki.
- `work_situations`: realne sytuacje zawodowe, interesariusze i konteksty użycia języka.
- `preferences`: preferencje dot. feedbacku, stylu i tempa pracy.
- `confidence_blockers`: stresory, blokady, rzeczy które odbierają płynność.
- `motivation_drivers`: powody, dla których uczniowi naprawdę zależy.
- `vocabulary_targets`: słowa lub obszary słownictwa, które warto ćwiczyć dalej.
- `tone_targets`: jak użytkownik chce brzmieć, np. spokojnie, executive, naturalnie.
- `recurring_weaknesses`: powtarzające się słabości komunikacyjne lub językowe, jeśli są już widoczne.
- Nie wpisuj chwilowych emocji, small talku ani domysłów.
- `core_guardrail_used`: ustaw na true, jeśli odpowiedź odmówiła gotowca, skróciła ryzyko lub wróciła do zasad.
- `core_guardrail_reason`: nazwij krótko, jaka zasada zadziałała.
""".strip()
    messages = [{"role": "system", "content": system_prompt}] + state.working.get_context_window()

    result = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        response_model=LiveTutorReply,
        max_retries=2,
    )

    lowered = user_message.lower()
    core_request_patterns = [
        "write the whole",
        "just copy",
        "do it for me",
        "napisz za mnie",
        "gotowca",
        "skopiować",
        "copy it",
    ]
    if not result.core_guardrail_used and any(pattern in lowered for pattern in core_request_patterns):
        result.core_guardrail_used = True
        result.core_guardrail_reason = "NO_HOMEWORK_SOLVING: wykryto prośbę o gotowca."

    apply_semantic_write(state, result.semantic_write)

    state.working.add("assistant", result.reply)
    return result


def compress_episode(state: AgentState, model: str = "gpt-4o-mini") -> Episode:
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in state.working.history)
    if not transcript.strip():
        return Episode(summary="Brak rozmowy do zapisania.", next_step="")

    messages = [
        {
            "role": "system",
            "content": (
                "Jesteś inżynierem pamięci. Wyciągnij esencję sesji do struktury. "
                "Jeśli w wypowiedziach ucznia są błędy gramatyczne lub językowe, "
                "KONIECZNIE zapisz każdy z nich w polu `mistakes` "
                "(user_phrase, correction, rule). "
                "Jeśli pojawiają się trwałe lub przydatne fakty o użytkowniku albo jego najbliższym kontekście, "
                "zapisz je w `personal_facts`. Ignoruj small talk, chyba że może pomóc w następnej sesji."
            ),
        },
        {"role": "user", "content": transcript},
    ]
    return client.chat.completions.create(
        model=model,
        response_model=Episode,
        messages=messages,
        temperature=0,
        max_retries=2,
    )


def merge_unique_text(existing: List[str], new_items: List[str], limit: int = 6) -> List[str]:
    merged = [item.strip() for item in existing if item.strip()]
    seen = set(merged)
    for item in new_items:
        clean = item.strip()
        if clean and clean not in seen:
            merged.append(clean)
            seen.add(clean)
    return merged[-limit:]


def refresh_context_summary(profile: StudentProfile) -> None:
    summary_bits: List[str] = []
    summary_bits.extend(profile.personal_context.relationship_hooks[-1:])
    summary_bits.extend(profile.personal_context.upcoming_events[-1:])
    summary_bits.extend(profile.semantic_signals.confidence_blockers[-1:])
    summary_bits.extend(profile.work_context.high_stakes_situations[-1:])
    summary_bits.extend(profile.semantic_signals.vocabulary_targets[-1:])
    summary_bits.extend(profile.performance_profile.tone_targets[-1:])
    if summary_bits:
        profile.context_notes = " | ".join(summary_bits[-5:])


def apply_semantic_write(state: AgentState, write: SemanticWriteCandidate) -> AgentState:
    state.profile.personal_context.personal_facts = merge_unique_text(
        state.profile.personal_context.personal_facts,
        write.personal_facts,
        limit=8,
    )
    state.profile.personal_context.relationship_hooks = merge_unique_text(
        state.profile.personal_context.relationship_hooks,
        write.relationship_hooks,
        limit=6,
    )
    state.profile.personal_context.upcoming_events = merge_unique_text(
        state.profile.personal_context.upcoming_events,
        write.upcoming_events,
        limit=6,
    )
    state.profile.work_context.high_stakes_situations = merge_unique_text(
        state.profile.work_context.high_stakes_situations,
        write.work_situations,
        limit=6,
    )
    state.profile.preferences = merge_unique_text(
        state.profile.preferences,
        write.preferences,
        limit=8,
    )
    state.profile.semantic_signals.confidence_blockers = merge_unique_text(
        state.profile.semantic_signals.confidence_blockers,
        write.confidence_blockers,
        limit=6,
    )
    state.profile.semantic_signals.motivation_drivers = merge_unique_text(
        state.profile.semantic_signals.motivation_drivers,
        write.motivation_drivers,
        limit=6,
    )
    state.profile.semantic_signals.vocabulary_targets = merge_unique_text(
        state.profile.semantic_signals.vocabulary_targets,
        write.vocabulary_targets,
        limit=8,
    )
    state.profile.performance_profile.tone_targets = merge_unique_text(
        state.profile.performance_profile.tone_targets,
        write.tone_targets,
        limit=6,
    )
    state.profile.performance_profile.recurring_weaknesses = merge_unique_text(
        state.profile.performance_profile.recurring_weaknesses,
        write.recurring_weaknesses,
        limit=6,
    )
    refresh_context_summary(state.profile)
    return state


def apply_episode_to_state(state: AgentState, episode: Episode) -> AgentState:
    state.episodic.add(episode)
    mistakes_as_text = [f"{m.user_phrase} -> {m.correction} ({m.rule})" for m in episode.mistakes]

    state.progress.recent_mistakes = mistakes_as_text[:5]
    state.progress.next_focus = episode.next_step or state.progress.next_focus
    if episode.next_step:
        state.progress.homework_status = "assigned"

    if episode.personal_facts:
        state.profile.personal_context.personal_facts = merge_unique_text(
            state.profile.personal_context.personal_facts,
            episode.personal_facts,
            limit=8,
        )
        refresh_context_summary(state.profile)
    return state


def start_session(
    state: AgentState,
    topic: str,
    phase: SessionPhase = SessionPhase.PRACTICE,
) -> AgentState:
    state.workflow.topic = topic
    state.workflow.phase = phase
    state.working = WorkingMemory()
    return state


def close_session(state: AgentState, model: str = "gpt-4o-mini") -> Episode:
    episode = compress_episode(state, model=model)
    apply_episode_to_state(state, episode)
    state.working = WorkingMemory()
    return episode


def preview_next_prompt(
    state: AgentState,
    topic: Optional[str] = None,
    phase: SessionPhase = SessionPhase.PRACTICE,
    limit: int = 1800,
) -> str:
    temp_state = state.model_copy(deep=True)
    temp_state.workflow.topic = topic or state.workflow.topic
    temp_state.workflow.phase = phase
    temp_state.working = WorkingMemory()
    prompt = build_prompt(temp_state)
    if len(prompt) <= limit:
        return prompt
    return prompt[:limit] + "\n\n...[ucięto podgląd]..."


# Legacy inspection helpers kept for older notebook drafts and offline debugging.
# Day 3 UI (`day3_v2.ipynb` + `app.py`) does not call them directly.
def render_state_markdown(state: AgentState) -> str:
    recent_episode = state.episodic.recent(1)
    latest_summary = recent_episode[0].summary if recent_episode else "brak"
    latest_next_step = recent_episode[0].next_step if recent_episode else "brak"
    recent_mistakes = "\n".join(f"- {item}" for item in state.progress.recent_mistakes) or "- brak"
    goals = "\n".join(
        f"- {goal.goal} | sukces: {goal.success_metric or 'brak'} | deadline: {goal.deadline or 'brak'}"
        for goal in state.profile.goals
    ) or "- brak"
    work_context = " | ".join(
        item
        for item in [
            state.profile.work_context.profession or "",
            state.profile.work_context.company_name or "",
            state.profile.work_context.industry or "",
        ]
        if item
    ) or "brak"
    high_stakes = ", ".join(state.profile.work_context.high_stakes_situations) or "brak"
    personal_hooks = ", ".join(state.profile.personal_context.relationship_hooks) or "brak"
    upcoming = ", ".join(state.profile.personal_context.upcoming_events) or "brak"
    blockers = ", ".join(state.profile.semantic_signals.confidence_blockers) or "brak"
    motivation = ", ".join(state.profile.semantic_signals.motivation_drivers) or "brak"
    vocabulary = ", ".join(state.profile.semantic_signals.vocabulary_targets) or "brak"
    tone_targets = ", ".join(state.profile.performance_profile.tone_targets) or "brak"
    weaknesses = ", ".join(state.profile.performance_profile.recurring_weaknesses) or "brak"

    return f"""### 👤 {state.profile.name} (`{state.profile.user_id}`)

**Profil**
- Poziom: `{state.profile.level}`
- Cel główny: {state.profile.goal}
- Język: `{state.profile.native_language} -> {state.profile.target_language}`
- Preferencje: {", ".join(state.profile.preferences) or "brak"}
- Work context: {work_context}
- High-stakes: {high_stakes}
- Haki relacyjne: {personal_hooks}
- Nadchodzące momenty: {upcoming}
- Blokery: {blockers}
- Motywatory: {motivation}
- Vocabulary targets: {vocabulary}
- Tone targets: {tone_targets}
- Recurring weaknesses: {weaknesses}
- Semantic summary: {state.profile.context_notes or "brak"}

**Cele operacyjne**
{goals}

**Postęp**
{recent_mistakes}
- Następny fokus: {state.progress.next_focus or "brak"}
- Status pracy domowej: `{state.progress.homework_status}`

**Sesja bieżąca**
- Faza: `{state.workflow.phase.value}`
- Temat: `{state.workflow.topic}`
- Wiadomości w Working Memory: `{len(state.working.history)}`

**Pamięć epizodyczna**
- Liczba epizodów: `{len(state.episodic.episodes)}`
- Ostatni summary: {latest_summary}
- Ostatni next_step: {latest_next_step or "brak"}
"""


def render_store_overview(store: "DemoMemoryStore") -> str:
    lines = ["### 🗂️ Demo store", ""]
    for user_id in store.user_ids():
        state = store.get(user_id)
        lines.append(
            f"- `{user_id}` → {state.profile.name} | epizody: `{len(state.episodic.episodes)}` | "
            f"next_focus: `{state.progress.next_focus or 'brak'}`"
        )
    return "\n".join(lines)


# Legacy utility for older drafts.
def phase_from_value(value: str) -> SessionPhase:
    return SessionPhase(value)


# Legacy utility for older drafts.
def state_checks(state: AgentState) -> Dict[str, bool]:
    has_context_memory = any(
        [
            bool(state.profile.context_notes),
            bool(state.profile.personal_context.personal_facts),
            bool(state.profile.personal_context.relationship_hooks),
            bool(state.profile.personal_context.upcoming_events),
            bool(state.profile.work_context.high_stakes_situations),
            bool(state.profile.semantic_signals.confidence_blockers),
            bool(state.profile.semantic_signals.vocabulary_targets),
        ]
    )
    return {
        "has_user_memory": bool(state.profile.name and state.profile.goal),
        "has_context_memory": has_context_memory,
        "has_progress_memory": bool(state.progress.recent_mistakes or state.progress.next_focus),
        "has_episodic_memory": len(state.episodic.episodes) > 0,
    }


def blank_memory_clone(user_id: str, topic: str) -> AgentState:
    shadow = seed_state_for_user(user_id)
    shadow.profile.goal = "Poćwiczyć angielski"
    shadow.profile.preferences = []
    shadow.profile.goals = []
    shadow.profile.work_context = WorkContext()
    shadow.profile.communication_preferences = CommunicationPreferences()
    shadow.profile.personal_context = PersonalContext()
    shadow.profile.semantic_signals = SemanticSignals()
    shadow.profile.performance_profile = PerformanceProfile()
    shadow.profile.context_notes = None
    shadow.progress = LearningProgress()
    shadow.episodic = EpisodicMemory()
    shadow.workflow.topic = topic
    shadow.workflow.phase = SessionPhase.PRACTICE
    shadow.working = WorkingMemory()
    return shadow


def seed_state_for_user(user_id: str) -> AgentState:
    if user_id == "marek_01":
        return AgentState(
            core=CoreMemory(),
            profile=StudentProfile(
                user_id="marek_01",
                name="Marek",
                level="B1",
                native_language="Polish",
                target_language="English",
                goal="Poprowadzić prezentację sprzedażową po angielsku w Nowym Jorku",
                preferences=["woli roleplay", "konkret zamiast wykładów o gramatyce"],
                goals=[
                    LearnerGoal(
                        goal="Otwierać prezentację sprzedażową bez czytania z notatek",
                        reason="Chce brzmieć pewnie przy klientach z USA",
                        target_situation="live product demo dla klientów z Nowego Jorku",
                        success_metric="pierwsze 90 sekund bez zacięcia i bez przejścia na polski",
                        deadline="2026-08-15",
                    )
                ],
                work_context=WorkContext(
                    profession="Senior Sales Manager",
                    company_name="NexLoop",
                    industry="B2B SaaS",
                    high_stakes_situations=[
                        "opening live product demos",
                        "Q&A z klientami z USA",
                    ],
                    stakeholders=["klienci enterprise z USA", "head of sales"],
                ),
                communication_preferences=CommunicationPreferences(
                    feedback_style="krótki i konkretny",
                    correction_intensity="najpierw płynność, potem korekta",
                    pace="dynamiczne sesje",
                    preferred_formats=["roleplay", "krótkie otwarcia", "symulacje pytań"],
                ),
                personal_context=PersonalContext(
                    personal_facts=["Ma kota o imieniu Luna."],
                    relationship_hooks=["Luna to dobry temat na przełamanie lodów."],
                    upcoming_events=["Za 5 tygodni prezentacja w Nowym Jorku."],
                    schedule_constraints=["Ćwiczy zwykle wieczorem po pracy."],
                ),
                semantic_signals=SemanticSignals(
                    confidence_blockers=[
                        "blokuje się w pierwszych 30 sekundach prezentacji",
                        "stresują go KPI i ocena szefa sprzedaży",
                    ],
                    motivation_drivers=[
                        "chce brzmieć pewnie przy klientach z USA",
                        "to ważny krok przed ekspansją zespołu za granicę",
                    ],
                    recurring_topics=["sales opening", "product demo", "value proposition"],
                    vocabulary_targets=["opening line", "benefits", "call to action"],
                ),
                performance_profile=PerformanceProfile(
                    target_scenarios=["sprzedażowe otwarcie prezentacji", "krótkie Q&A po demo"],
                    tone_targets=["spokojnie", "pewnie", "naturalnie"],
                    strengths=["dobrze zna produkt", "jest konkretny"],
                    recurring_weaknesses=["za szybko przechodzi do pitchu", "gubi prostą składnię pod stresem"],
                    success_criteria=["otwarcie bez notatek", "mniej dosłownych tłumaczeń z polskiego"],
                ),
                context_notes=(
                    "Luna to dobry temat na przełamanie lodów. | "
                    "Za 5 tygodni prezentacja w Nowym Jorku. | "
                    "Blokuje się w pierwszych 30 sekundach prezentacji."
                ),
            ),
            progress=LearningProgress(),
            workflow=WorkflowMemory(topic="Opening a sales presentation"),
            working=WorkingMemory(),
            episodic=EpisodicMemory(),
        )
    if user_id == "ania_02":
        return AgentState(
            core=CoreMemory(),
            profile=StudentProfile(
                user_id="ania_02",
                name="Ania",
                level="B2",
                native_language="Polish",
                target_language="English",
                goal="Przejść rozmowę rekrutacyjną po angielsku do londyńskiego startupu",
                preferences=["krótkie korekty", "najpierw praktyka, potem teoria"],
                goals=[
                    LearnerGoal(
                        goal="Przejść przez pierwsze 5 minut rozmowy rekrutacyjnej bez utraty pewności siebie",
                        reason="Rekrutuje się do startupu product-led w Londynie",
                        target_situation="intro + odpowiedź na Tell me about yourself",
                        success_metric="spójna odpowiedź 60-90 sekund bez długich pauz",
                        deadline="2026-07-29",
                    )
                ],
                work_context=WorkContext(
                    profession="Product Designer",
                    company_name="Freelance / transition",
                    industry="SaaS / startup",
                    high_stakes_situations=[
                        "intro na rozmowie rekrutacyjnej",
                        "opowiadanie o swoim portfolio po angielsku",
                    ],
                    stakeholders=["hiring manager", "founder", "head of product"],
                ),
                communication_preferences=CommunicationPreferences(
                    feedback_style="bezpośredni, ale spokojny",
                    correction_intensity="mało przerywania w trakcie mówienia",
                    pace="średnie tempo",
                    preferred_formats=["mock interview", "krótkie follow-up questions"],
                ),
                personal_context=PersonalContext(
                    personal_facts=["Lubi porządkować odpowiedzi według prostego schematu."],
                    relationship_hooks=["Dobrze reaguje na krótkie, konkretne podsumowania."],
                    upcoming_events=["W przyszłym tygodniu ma rozmowę do londyńskiego startupu."],
                    schedule_constraints=["Najlepiej uczy się rano."],
                ),
                semantic_signals=SemanticSignals(
                    confidence_blockers=[
                        "stresuje się, gdy ma mówić o sobie bez przygotowania",
                        "nie lubi długich monologów zwrotnych",
                    ],
                    motivation_drivers=[
                        "chce wejść do międzynarodowego startupu",
                        "chce mówić bardziej dojrzale i pewnie",
                    ],
                    recurring_topics=["job interview", "portfolio story", "strengths"],
                    vocabulary_targets=["impact", "ownership", "decision-making"],
                ),
                performance_profile=PerformanceProfile(
                    target_scenarios=["Tell me about yourself", "portfolio walkthrough"],
                    tone_targets=["spójnie", "dojrzale", "bez chaosu"],
                    strengths=["myśli strukturalnie", "dobrze opowiada o procesie"],
                    recurring_weaknesses=["za szybko traci wątek przy pytaniach otwartych"],
                    success_criteria=["krótka, pewna odpowiedź na start rozmowy"],
                ),
                context_notes=(
                    "W przyszłym tygodniu ma rozmowę do londyńskiego startupu. | "
                    "Stresuje się, gdy ma mówić o sobie bez przygotowania. | "
                    "Dobrze reaguje na krótkie, konkretne podsumowania."
                ),
            ),
            progress=LearningProgress(),
            workflow=WorkflowMemory(topic="Job interview opening"),
            working=WorkingMemory(),
            episodic=EpisodicMemory(),
        )
    raise KeyError(f"Nieznany demo user_id: {user_id}")


class DemoMemoryStore:
    def __init__(self, seed_user_ids: Optional[List[str]] = None):
        user_ids = seed_user_ids or ["marek_01", "ania_02"]
        self._seed: Dict[str, AgentState] = {user_id: seed_state_for_user(user_id) for user_id in user_ids}
        self._states: Dict[str, AgentState] = deepcopy(self._seed)

    def get(self, user_id: str) -> AgentState:
        return self._states[user_id]

    def user_ids(self) -> List[str]:
        return sorted(self._states.keys())

    def reset_user(self, user_id: str) -> AgentState:
        self._states[user_id] = deepcopy(self._seed[user_id])
        return self._states[user_id]


def seed_demo_store() -> DemoMemoryStore:
    return DemoMemoryStore()
