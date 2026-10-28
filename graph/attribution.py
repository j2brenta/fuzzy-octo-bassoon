"""Source-grounded speakers, separate from the writer, forwarder, and people mentioned."""
from engine.extract import source_passages
from engine.llm import ModelOutputError
from refresher.schemas import Attribution, Span


# These labels describe a role or missing metadata, not an identified person.
PLACEHOLDER_NAMES = {'writer', 'the writer', 'author', 'the author', 'unknown', 'unknown author', 'anonymous'}


def is_named_person(name):
    return isinstance(name, str) and bool(name.strip()) and ' '.join(name.casefold().split()) not in PLACEHOLDER_NAMES


def author_name(source):
    name = source.author_hint.name if source.author_hint else None
    return name if is_named_person(name) else None


def validate(attribution, source, entities, claim, text):
    """Recheck stored attribution on replay; exact spans and a known identity are required."""
    if attribution is None or attribution.kind == "unknown":
        return attribution
    if not is_named_person(attribution.person_name):
        raise ValueError("A role or placeholder cannot identify a person.")
    if attribution.kind == "author":
        if author_name(source) != attribution.person_name:
            raise ValueError("Attributed author does not match source metadata.")
    else:
        entity = next((e for e in entities if e.kind == "person" and e.name == attribution.person_name), None)
        if entity is None or not is_named_person(entity.base_name or entity.name):
            raise ValueError("Attributed speaker is not a source person.")
    passages = {(p["start"], p["end"]) for p in source_passages(text)}
    for span in attribution.evidence:
        if (span.item_id != claim.spans[0].item_id or span.section_id != claim.spans[0].section_id
                or (span.start_char, span.end_char) not in passages):
            raise ValueError("Attribution evidence does not match an exact source passage.")
    if not any(a.start_char < c.end_char and c.start_char < a.end_char
               for a in attribution.evidence for c in claim.spans):
        raise ValueError("Attribution must cite a passage supporting the claim.")
    if attribution.kind == "quoted":
        cited = "\n".join(text[s.start_char:s.end_char] for s in attribution.evidence).casefold()
        if not any(name.casefold() in cited for name in [entity.name, *entity.aliases]):
            raise ValueError("Speaker name or source alias is missing from attribution evidence.")
    return attribution


def from_model(value, item, claim):
    if value is None:
        return None  # old relation replies remain readable, with attribution unknown
    if not isinstance(value, dict) or set(value) != {"kind", "person", "passages"}:
        raise ModelOutputError("Speaker attribution needs kind, person and passages.")
    kind, person, ids = value["kind"], value["person"], value["passages"]
    if kind == "unknown":
        if person is not None or ids != []:
            raise ModelOutputError("Unknown speakers must have no person or passages.")
        return Attribution(kind="unknown")
    if kind == "author":
        name = author_name(item.source)
        if person != "author" or not name:
            raise ModelOutputError("Author attribution requires a named source author.")
    elif kind == "quoted":
        people = {f"p{n}": e.name for n, e in enumerate((e for e in item.entities if e.kind == "person"), 1) if is_named_person(e.name)}
        if not isinstance(person, str) or person not in people:
            raise ModelOutputError("Quoted speakers must be supplied person IDs.")
        name = people[person]
    else:
        raise ModelOutputError("Unknown attribution kind.")
    passages = {p["id"]: p for p in source_passages(item.text)}
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 10 or any(type(i) is not int or i not in passages for i in ids)
            or len(set(ids)) != len(ids)):
        raise ModelOutputError("Attribution needs one to ten distinct source passage IDs.")
    spans = [Span(item_id=claim.spans[0].item_id, section_id=claim.spans[0].section_id,
                  start_char=passages[i]["start"], end_char=passages[i]["end"]) for i in sorted(ids)]
    try:
        return validate(Attribution(kind=kind, person_name=name, evidence=spans), item.source,
                        item.entities, claim, item.text)
    except ValueError as error:
        raise ModelOutputError(str(error)) from None
