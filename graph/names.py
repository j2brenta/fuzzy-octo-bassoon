"""Whether two written names can be one person: across Cyrillic and Latin script, Russian short forms, initials,
and "Last First" order. Only proposes; the user decides a merge (graph.memory review)."""
import re

from graph.relate import words

# "Krestnikov; Konstantin", "van der Berg, Jan", "Gal, Y.": one surname (with lowercase particles), then one or two
# given names. Citation metadata and trafilatura write a name this way; people read "First Last".
_NAME_WORD = r"[^\W\d_][\w.'’-]*"
_CITED = re.compile(rf"^((?:{_NAME_WORD}\s+)*{_NAME_WORD})\s*[;,]\s*({_NAME_WORD}(?:\s+{_NAME_WORD})?)$")

_LATIN = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                   "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
# Spellings transliteration schemes disagree on, folded to one: Aleksey/Alexey/Aleksei, Evgeny/Yevgeniy.
_FOLD = (("shch", "sch"), ("kh", "h"), ("x", "ks"), ("j", "y"), ("w", "v"), ("ph", "f"), ("iy", "y"), ("ii", "y"),
         ("ei", "ey"), ("yi", "y"), ("ye", "e"), ("yo", "e"))
# Russian short forms with the full first name they stand for. One short form can stand for several (Слава).
SHORT_FORMS = {
    "саша": ("александр", "александра"), "шура": ("александр", "александра"), "лёша": ("алексей",),
    "леша": ("алексей",), "алёша": ("алексей",), "андрюша": ("андрей",), "аня": ("анна",), "настя": ("анастасия",),
    "вася": ("василий",), "витя": ("виктор",), "вова": ("владимир",), "володя": ("владимир",),
    "слава": ("вячеслав", "владислав", "ярослав", "святослав"), "влад": ("владислав",), "галя": ("галина",),
    "гена": ("геннадий",), "гоша": ("георгий",), "жора": ("георгий",), "гриша": ("григорий",), "даша": ("дарья",),
    "дима": ("дмитрий",), "митя": ("дмитрий",), "женя": ("евгений", "евгения"), "катя": ("екатерина",),
    "лена": ("елена",), "ваня": ("иван",), "ира": ("ирина",), "костя": ("константин",),
    "ксюша": ("ксения",), "лёва": ("лев",), "лева": ("лев",), "люба": ("любовь",), "люда": ("людмила",),
    "маша": ("мария",), "миша": ("михаил",), "надя": ("надежда",), "наташа": ("наталья",),
    "коля": ("николай",), "оля": ("ольга",), "паша": ("павел",), "петя": ("пётр", "петр"), "рома": ("роман",),
    "серёжа": ("сергей",), "сережа": ("сергей",), "света": ("светлана",), "стас": ("станислав",),
    "таня": ("татьяна",), "толя": ("анатолий",), "федя": ("фёдор", "федор"), "юля": ("юлия",), "юра": ("юрий",),
}


def display_name(name: str) -> str:
    """A person's name as people write it: "Last; First" and "Last, First" become "First Last"; anything else is
    kept, trimmed. Two names in a list ("Ada Lovelace; Alan Turing") are not one cited name and stay as they are."""
    cited = _CITED.match(name.strip())
    if not cited:
        return name.strip()
    *particles, surname = cited.group(1).split()
    given = cited.group(2).split()
    if not surname[0].isupper() or not all(w[0].isupper() for w in given) or not all(w.islower() for w in particles):
        return name.strip()
    return f"{cited.group(2)} {cited.group(1)}"


def latin(word: str) -> str:
    """A casefolded word in Latin letters, with spelling variants folded, so Крестников and Krestnikov meet."""
    out = "".join(_LATIN.get(ch, ch) for ch in word.casefold())
    for a, b in _FOLD:
        out = out.replace(a, b)
    return out


def _first_names(word: str) -> set[str]:
    """The first name as written, and the full names a Russian short form stands for, in Latin letters."""
    return {latin(word)} | {latin(full) for full in SHORT_FORMS.get(word.casefold(), ())} \
        | {latin(full) for short, fulls in SHORT_FORMS.items() if latin(short) == latin(word) for full in fulls}


def _match(first_a: str, last_a: str, first_b: str, last_b: str) -> bool:
    if latin(last_a) != latin(last_b):
        return False
    fa, fb = latin(first_a), latin(first_b)
    if min(len(fa), len(fb)) == 1:  # "Y. Gal" and "Yarin Gal"
        return fa[0] == fb[0]
    return bool(_first_names(first_a) & _first_names(first_b))


def same_person(a: str, b: str) -> bool:
    """"Y. Gal" and "Yarin Gal", "Костя Крестников" and "Konstantin Krestnikov", "Krestnikov Konstantin" and
    "Konstantin Krestnikov": the same last name, and first names that are equal, an initial of the other, or a
    short form of it. Names already spelled the same are one person id and never need this."""
    wa, wb = words(a), words(b)
    if len(wa) < 2 or len(wb) < 2 or wa == wb:
        return False
    if _match(wa[0], wa[-1], wb[0], wb[-1]):
        return True
    # "Last First" as citation metadata and some sources write it: two words each, crossed.
    return len(wa) == len(wb) == 2 and latin(wa[0]) != latin(wa[1]) and _match(wa[0], wa[1], wb[1], wb[0])
