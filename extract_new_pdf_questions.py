import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from pypdf import PdfReader


PDF_PATH = Path("1774786904430382.pdf")
OUTPUT_PATH = Path("tests_additional.json")

MAIN_X_MIN = 568
MAIN_X_MAX = 1225
Y_MIN = -300
Y_MAX = 950

INSTRUCTION_RE = re.compile(r"Выберите\s+один(?:\s+или\s+несколько)?\s+ответ", re.I)
CHECK_RE = re.compile(r"проверить", re.I)
CORRECT_RE = re.compile(r"правильн\w*\s+ответ\s*[:;]", re.I)

MATCH_TRANSLATION = str.maketrans(
    {
        "A": "а",
        "B": "в",
        "C": "с",
        "E": "е",
        "H": "н",
        "K": "к",
        "M": "м",
        "O": "о",
        "P": "р",
        "T": "т",
        "X": "х",
        "Y": "у",
        "a": "а",
        "c": "с",
        "e": "е",
        "o": "о",
        "p": "р",
        "x": "х",
        "y": "у",
        "0": "о",
        "ё": "е",
        "Ё": "е",
    }
)


def clean_line(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = text.replace(" \u00ad ", "-").replace("\u00ad", "-")
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"-\s+", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = re.sub(r"([(<])\s+", r"\1", text)
    text = re.sub(r"\s+([)>])", r"\1", text)
    return text


def normalize_for_match(text: str) -> str:
    text = clean_line(text).translate(MATCH_TRANSLATION).lower()
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def line_items(page) -> list[dict]:
    items: list[tuple[float, float, str]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        raw = text.strip("\n")
        if not raw.strip():
            return

        x, y = tm[4], tm[5]
        if MAIN_X_MIN <= x <= MAIN_X_MAX and Y_MIN <= y <= Y_MAX:
            items.append((x, y, raw))

    page.extract_text(visitor_text=visitor)

    groups: list[dict] = []
    for x, y, text in items:
        for group in groups:
            if abs(group["y"] - y) <= 3:
                group["items"].append((x, text))
                group["y"] = (group["y"] + y) / 2
                break
        else:
            groups.append({"y": y, "items": [(x, text)]})

    lines = []
    for group in groups:
        sorted_items = sorted(group["items"])
        text = clean_line(" ".join(value for _, value in sorted_items))
        if text:
            lines.append({"y": group["y"], "items": sorted_items, "text": text})

    return sorted(lines, key=lambda line: -line["y"])


def visual_mark(text: str) -> str | None:
    cleaned = clean_line(text)
    if re.fullmatch(r"[VvY✓]", cleaned):
        return "правильно"
    if re.fullmatch(r"[xXхХ]", cleaned):
        return "неправильно"
    if re.search(r"\s+(?:[VvY✓]|ч/|v['’]?|V['’]?|у['’]?|[уУyYvVлЛяЯ5Sѕ][/-]|[,،]/|[уУyYvVлЛяЯ5Sѕ]\")$", cleaned):
        return "правильно"
    if re.search(r"[,،]/$", cleaned):
        return "правильно"
    if re.search(r"\s+(?:[xXхХ])$", cleaned):
        return "неправильно"
    if re.search(r"^\s*(?:[VvY▼✓]|ч/)", cleaned):
        return "правильно"
    return None


def strip_answer_marks(line: dict) -> tuple[str, bool]:
    items = line["items"]
    prefix_removed = False

    if len(items) > 1 and items[0][0] < 590 and len(clean_line(items[0][1])) <= 5 and items[1][0] >= 590:
        text = clean_line(" ".join(value for _, value in items[1:]))
        prefix_removed = True
    else:
        text = clean_line(line["text"])

    before = text
    text = re.sub(
        r"^(?:[\^?7СсЗ]|[•·■□▯]|[VvY▼✓]|ч/)\s+(?=[ВвНнПпАаОоДдУуИиКкТт])",
        "",
        text,
    ).strip()
    if text != before:
        prefix_removed = True

    leading_artifact = re.match(r"^([0-9ТГр\s,'\"’`|^]+)([А-Яа-яЁё].*)$", text)
    if leading_artifact and any(char in leading_artifact.group(1) for char in "^|'\"’`|"):
        text = leading_artifact.group(2).strip()
        prefix_removed = True

    text = re.sub(r"^(?:(?:[\^•·■□▯]+)|[VvY▼✓]|ч/)\s*(?=\S)", "", text).strip()
    text = re.sub(
        r"\s+(?:[VvY✓]|[xXхХ]|ч/|v['’]?|V['’]?|у['’]?|[уУyYvVлЛяЯ5Sѕ][/-]|[,،]/|[уУyYvVлЛяЯ5Sѕ]\")$",
        "",
        text,
    ).strip()
    text = re.sub(r"(?<=[А-Яа-яA-Za-z)])[,،]/$", "", text).strip()
    text = re.sub(r"(?<=/л)[xXхХ]$", "", text).strip()
    text = re.sub(r"(?<=[А-Яа-яA-Za-z)])\^$", "", text).strip()
    text = re.sub(r"^([А-ЯA-Z0-9]+)[vVхХxX]$", r"\1", text).strip()
    return clean_line(text), prefix_removed


def split_answers(answer_lines: list[dict]) -> list[dict]:
    answers: list[dict] = []
    current_text: list[str] = []
    current_marks: list[str] = []
    previous_y = None

    for line in answer_lines:
        gap = previous_y - line["y"] if previous_y is not None else 0
        should_split = previous_y is not None and (gap > 29 or line.get("starts_option"))
        if should_split and current_text:
            answers.append(
                {
                    "text": clean_line(" ".join(current_text)),
                    "visual_marks": [mark for mark in current_marks if mark],
                }
            )
            current_text = []
            current_marks = []

        current_text.append(line["text"])
        current_marks.append(line.get("visual_mark"))
        previous_y = line["y"]

    if current_text:
        answers.append(
            {
                "text": clean_line(" ".join(current_text)),
                "visual_marks": [mark for mark in current_marks if mark],
            }
        )

    return [answer for answer in answers if answer["text"]]


def mark_answers(answers: list[str], correct_text: str, instruction: str) -> list[str]:
    correct_norm = normalize_for_match(correct_text)
    answer_norms = [normalize_for_match(answer) for answer in answers]

    candidates: list[tuple[int, int, int, int]] = []
    for answer_index, answer_norm in enumerate(answer_norms):
        if not answer_norm:
            continue

        start = 0
        while True:
            position = correct_norm.find(answer_norm, start)
            if position == -1:
                break
            candidates.append((len(answer_norm), answer_index, position, position + len(answer_norm)))
            start = position + 1

    chosen: list[int] = []
    occupied: list[tuple[int, int]] = []
    for _, answer_index, start, end in sorted(candidates, reverse=True):
        if any(not (end <= used_start or start >= used_end) for used_start, used_end in occupied):
            continue
        chosen.append(answer_index)
        occupied.append((start, end))

    marks = ["неправильно"] * len(answers)
    for answer_index in chosen:
        marks[answer_index] = "правильно"

    if "несколько" not in instruction.lower():
        if marks.count("правильно") != 1 and correct_norm:
            scores = []
            for answer_index, answer_norm in enumerate(answer_norms):
                matcher = SequenceMatcher(None, answer_norm, correct_norm)
                ratio = matcher.ratio()
                coverage = sum(block.size for block in matcher.get_matching_blocks()) / max(1, len(answer_norm))
                scores.append((max(ratio, coverage), answer_index))

            best_score, best_index = max(scores) if scores else (0, None)
            marks = ["неправильно"] * len(answers)
            if best_index is not None and best_score > 0.45:
                marks[best_index] = "правильно"
    elif marks.count("правильно") == 0 and correct_norm:
        for answer_index, answer_norm in enumerate(answer_norms):
            matcher = SequenceMatcher(None, answer_norm, correct_norm)
            coverage = sum(block.size for block in matcher.get_matching_blocks()) / max(1, len(answer_norm))
            if coverage > 0.92:
                marks[answer_index] = "правильно"

    if "несколько" in instruction.lower() and correct_norm:
        for answer_index, answer_norm in enumerate(answer_norms):
            if len(correct_norm) > 20 and answer_norm.startswith(correct_norm):
                marks[answer_index] = "правильно"

    return marks


def extract_page(reader: PdfReader, page_index: int) -> dict:
    lines = line_items(reader.pages[page_index])
    instruction_index = next(
        (index for index, line in enumerate(lines) if INSTRUCTION_RE.search(line["text"])),
        None,
    )
    if instruction_index is None:
        raise ValueError("instruction not found")

    question_lines: list[str] = []
    last_y = lines[instruction_index]["y"]
    for line_index in range(instruction_index - 1, -1, -1):
        gap = lines[line_index]["y"] - last_y
        if gap > 90:
            break

        text = lines[line_index]["text"]
        if any(marker in text for marker in ["Тренировочный", "(фармацевтическим) образованием", "Врач общей"]):
            break

        question_lines.append(text)
        last_y = lines[line_index]["y"]

    question = clean_line(" ".join(reversed(question_lines)))
    if question.endswith(";"):
        question = f"{question[:-1]}:"

    instruction = (
        "Выберите один или несколько ответов:"
        if "несколько" in lines[instruction_index]["text"].lower()
        else "Выберите один ответ:"
    )

    check_index = next(
        (index for index in range(instruction_index + 1, len(lines)) if CHECK_RE.search(lines[index]["text"])),
        None,
    )
    correct_index = next(
        (index for index in range(instruction_index + 1, len(lines)) if CORRECT_RE.search(lines[index]["text"])),
        None,
    )
    end_index = check_index if check_index is not None else correct_index
    if end_index is None:
        raise ValueError("answer end not found")

    answer_lines = []
    for line in lines[instruction_index + 1 : end_index]:
        mark = visual_mark(line["text"])
        if re.fullmatch(r"[VvY✓xXхХ]", clean_line(line["text"])):
            if answer_lines and mark:
                answer_lines[-1]["visual_mark"] = mark
            continue

        text, prefix_removed = strip_answer_marks(line)
        if text and not INSTRUCTION_RE.search(text) and not CHECK_RE.search(text):
            answer_lines.append(
                {
                    "y": line["y"],
                    "text": text,
                    "visual_mark": mark,
                    "starts_option": prefix_removed or bool(re.match(r"^\s*(?:[VvY▼✓]|ч/)\s*\S+", line["text"])),
                }
            )

    answers = split_answers(answer_lines)
    answer_texts = [answer["text"] for answer in answers]

    if correct_index is None:
        if page_index + 1 != 575:
            raise ValueError("correct answer not found")
        marks = ["неправильно" if "неправильно" in answer["visual_marks"] else "правильно" for answer in answers]
    else:
        correct_parts = []
        first_correct_line = CORRECT_RE.sub("", lines[correct_index]["text"]).strip()
        if first_correct_line:
            correct_parts.append(first_correct_line)

        for line in lines[correct_index + 1 :]:
            text = clean_line(line["text"])
            if text and not CHECK_RE.search(text):
                correct_parts.append(text)

        correct_text = clean_line(" ".join(correct_parts))
        marks = mark_answers(answer_texts, correct_text, instruction)
        for answer_index, answer in enumerate(answers):
            if "несколько" in instruction.lower() and "правильно" in answer["visual_marks"]:
                marks[answer_index] = "правильно"

    return {
        "page": page_index + 1,
        "question_number": page_index + 1,
        "question": question,
        "instruction": instruction,
        "answers": [
            {"text": answer["text"], "mark": mark}
            for answer, mark in zip(answers, marks)
        ],
    }


def set_answer_text(question: dict, answer_index: int, text: str) -> None:
    question["answers"][answer_index]["text"] = text


def apply_manual_fixes(question: dict) -> dict:
    number = question["question_number"]

    if number == 2:
        set_answer_text(question, 1, "Ингаляционный β2-адреномиметик")

    if number == 103:
        question["answers"] = [
            {"text": "тромбоцитопения более 20×10⁹/л без геморрагического синдрома", "mark": "неправильно"},
            {"text": "тромбостения Гланцмана", "mark": "неправильно"},
            {"text": "тромбоцитопения менее 10×10⁹/л", "mark": "правильно"},
            {
                "text": "тромбоцитопения менее 40×10⁹/л с геморрагическим синдромом на фоне цитостатической терапии",
                "mark": "правильно",
            },
        ]

    if number == 122:
        question["answers"] = [
            {"text": "10,9-20,0×10⁹/л", "mark": "неправильно"},
            {"text": "2,5-3,8×10⁹/л", "mark": "неправильно"},
            {"text": "1,5-3,5×10⁹/л", "mark": "неправильно"},
            {"text": "4,0-9,0×10⁹/л", "mark": "правильно"},
            {"text": "3,9-15,9×10⁹/л", "mark": "неправильно"},
        ]

    if number == 123:
        question["answers"] = [
            {"text": "150-450×10⁹/л", "mark": "правильно"},
            {"text": "200-400×10⁹/л", "mark": "неправильно"},
            {"text": "100-300×10⁹/л", "mark": "неправильно"},
            {"text": "180-320×10⁹/л", "mark": "неправильно"},
            {"text": "160-400×10⁹/л", "mark": "неправильно"},
        ]

    if number == 142:
        question["answers"] = [
            {"text": "менее 80×10⁹/л", "mark": "неправильно"},
            {"text": "менее 200×10⁹/л", "mark": "неправильно"},
            {"text": "менее 400×10⁹/л", "mark": "неправильно"},
            {"text": "менее 100×10⁹/л", "mark": "неправильно"},
            {"text": "менее 50×10⁹/л", "mark": "правильно"},
        ]

    if number == 584:
        set_answer_text(question, 3, "Температура тела пациента не ниже 32,2° C")

    if number == 586:
        set_answer_text(question, 3, "Гипотермия (температура тела менее 32° C)")

    if number == 624:
        question["question"] = question["question"].replace("Объективно: 8 сознании", "Объективно: В сознании")

    if number == 391:
        question["question"] = question["question"].replace("” А\"", "\"А\"")

    if number == 392:
        question["question"] = "\"Тройной прием Сафара\" проводится для:"

    if number == 395:
        question["answers"][0]["text"] = question["answers"][0]["text"].replace(
            "пространстве н при сформированном ’ ’ неправильном прикусе",
            "пространстве и при сформированном неправильном прикусе",
        )

    if number == 408:
        question["question"] = question["question"].replace("\"Н ”", "\"Н\"")

    replacements = {
        "COVID-1 9": "COVID-19",
        "7-1 0 дней": "7-10 дней",
        "2типа": "2 типа",
        "строке Ь)": "строке b)",
        "(1) пункта 8": "(I) пункта 8",
    }
    for source, target in replacements.items():
        question["question"] = question["question"].replace(source, target)
        for answer in question["answers"]:
            answer["text"] = answer["text"].replace(source, target)

    return question


def main() -> None:
    reader = PdfReader(str(PDF_PATH))
    questions = [apply_manual_fixes(extract_page(reader, page_index)) for page_index in range(len(reader.pages))]
    OUTPUT_PATH.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"written: {OUTPUT_PATH}")
    print(f"questions: {len(questions)}")


if __name__ == "__main__":
    main()
