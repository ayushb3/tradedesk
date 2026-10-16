"""Prompt builders for the course offline generation passes (M8c, issue #54).

Pure functions — no I/O, no LLM calls. `build_concept_prompt` embeds the allowed
seed vocabulary (slugs + names + categories) directly into the system prompt and
instructs the model to use ONLY those slugs and to emit a single JSON object with
no prose or code fences. The extraction orchestrator (`generate.py`) still
drift-guards the output against the seed set, so the prompt constraint is
belt-and-suspenders.
"""
from __future__ import annotations

from app.knowledge.seed import SeedVocab

# Cap the transcript we send so a single lesson stays well within token limits.
_MAX_RAW_CHARS = 6000


def build_concept_prompt(lesson: dict, seed: SeedVocab) -> tuple[str, str]:
    """Return (system_prompt, user_message) for the concept-extraction pass.

    ``lesson`` has keys: id, module, seq, title, raw_text.
    """
    # Render the allowed vocabulary as a compact reference table. Aliases are
    # included so a transcript using a synonym (e.g. "S/R") maps to the canonical
    # slug (support-resistance) — improves recall; the drift guard still filters to slugs.
    vocab_lines = []
    for slug in sorted(seed.concepts):
        c = seed.concepts[slug]
        aliases = c.get("aliases") or []
        alias_str = f" | aliases: {', '.join(aliases)}" if aliases else ""
        vocab_lines.append(f"- {slug} | {c['name']} | {c['category']}{alias_str}")
    vocab_block = "\n".join(vocab_lines)

    relations = "related | prerequisite | contrast | part_of"

    system = (
        "You are a trading-education analyst tagging a lesson transcript with the "
        "concepts it teaches. You must use ONLY the concept slugs from the "
        "controlled vocabulary below. Do NOT invent new concepts or slugs — any "
        "concept not in this list will be discarded.\n\n"
        "ALLOWED CONCEPTS (slug | name | category | aliases):\n"
        f"{vocab_block}\n\n"
        "Return a SINGLE JSON object and nothing else — no prose, no markdown, no "
        "code fences. Shape:\n"
        '{"concepts": [{"slug": "<allowed-slug>", "weight": <0.0-1.0 salience>}], '
        '"links": [{"a_slug": "<allowed-slug>", "b_slug": "<allowed-slug>", '
        f'"relation": "<one of: {relations}>"}}]}}\n\n'
        "Rules:\n"
        "- Only include concepts genuinely taught or discussed in THIS lesson.\n"
        "- weight reflects how central the concept is to the lesson (1.0 = core "
        "topic, 0.3 = mentioned in passing).\n"
        "- links are optional; only add a link when the lesson explicitly relates "
        "two allowed concepts. Omit links entirely if unsure (use []).\n"
        "- If the lesson teaches no allowed concept, return "
        '{"concepts": [], "links": []}.'
    )

    raw = (lesson.get("raw_text") or "")[:_MAX_RAW_CHARS]
    user = (
        f"Lesson {lesson.get('id')} (Module {lesson.get('module')}, "
        f"seq {lesson.get('seq')}): {lesson.get('title')}\n\n"
        f"Transcript:\n{raw}"
    )
    return system, user


def build_sheet_prompt(lesson: dict) -> tuple[str, str]:
    """Return (system_prompt, user_message) for the study-sheet generation pass.

    ``lesson`` has keys: id, module, seq, title, raw_text. The model is asked to
    distil the (often auto-captioned, messy) transcript into a compact study sheet
    as a SINGLE JSON object — no prose, no code fences.
    """
    system = (
        "You are a trading-education editor turning a lesson transcript into a "
        "concise STUDY SHEET a student can revise from. The transcript may be a "
        "rough auto-generated caption — infer the teaching intent and clean it up.\n\n"
        "Return a SINGLE JSON object and nothing else — no prose, no markdown "
        "outside the JSON string values, no code fences. Shape:\n"
        '{"summary_md": "<one short paragraph>", '
        '"rules_md": "<markdown bullet list of the key rules>", '
        '"diagram_mermaid": "<valid Mermaid flowchart source OR null>", '
        '"quiz": [{"q": "<question>", "choices": ["<a>", "<b>", ...], '
        '"answer_idx": <0-based index of the correct choice>, '
        '"explanation": "<why that answer is correct>"}]}\n\n'
        "Rules:\n"
        "- summary_md: ONE short paragraph capturing the core idea of the lesson.\n"
        "- rules_md: markdown bullets ('- ...') of the concrete rules / takeaways "
        "the lesson teaches. Keep each bullet tight.\n"
        "- diagram_mermaid: a `flowchart TD` (top-down) Mermaid diagram of the "
        "decision/process the lesson describes, as raw Mermaid source. If the "
        "lesson does not lend itself to a clean flowchart, use null — do NOT "
        "force a diagram.\n"
        "- quiz: 2 to 4 multiple-choice questions checking the lesson's key "
        "points. Each has 2+ choices, a 0-based answer_idx into that list, and a "
        "brief explanation.\n"
        "- Base everything ONLY on THIS lesson's transcript. Do not invent "
        "material the lesson doesn't cover."
    )

    raw = (lesson.get("raw_text") or "")[:_MAX_RAW_CHARS]
    user = (
        f"Lesson {lesson.get('id')} (Module {lesson.get('module')}, "
        f"seq {lesson.get('seq')}): {lesson.get('title')}\n\n"
        f"Transcript:\n{raw}"
    )
    return system, user
