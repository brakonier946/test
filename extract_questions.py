import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from pypdf import PdfReader


PDF_PATH = Path("tests.pdf")
FIRST_TEN_PATH = Path("tests_first_10.json")
OUTPUT_PATH = Path("tests_all.json")

MAIN_X_MIN = 340
MAIN_X_MAX = 955
Y_MIN = -40
Y_MAX = 760

INSTRUCTION_RE = re.compile(r"Выберите\s+один(?:\s+или\s+несколько)?\s+ответ", re.I)
CHECK_RE = re.compile(r"проверить", re.I)
CORRECT_RE = re.compile(r"правильн\w*\s+ответ\s*:", re.I)

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
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"-\s+", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = re.sub(r"([(<])\s+", r"\1", text)
    text = re.sub(r"\s+([)>])", r"\1", text)
    return text


def remove_answer_marks(text: str) -> str:
    text = clean_line(text)
    text = re.sub(r"^(?:СЗ|са)\s+", "", text).strip()
    text = re.sub(r"^(?:[•·]|[VvY▼✓])\s+", "", text).strip()
    text = re.sub(r"\s+(?:[VvY✓]|[xXхХ]|ч/|v['’]?|V['’]?)$", "", text).strip()
    text = re.sub(r"\s+[vVхХxX]['’]?$", "", text).strip()
    text = re.sub(r"^([А-ЯA-Z0-9]+)[vVхХxX]$", r"\1", text).strip()
    return clean_line(text)


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
        text = clean_line(" ".join(value for _, value in sorted(group["items"])))
        if text:
            lines.append({"y": group["y"], "text": text})

    return sorted(lines, key=lambda line: -line["y"])


def visual_mark(text: str) -> str | None:
    cleaned = clean_line(text)
    if re.search(r"^\s*(?:[VvY▼✓]|ч/)\s+", cleaned):
        return "правильно"
    if re.search(r"\s+(?:[VvY✓]|ч/|v['’]?|V['’]?)$", cleaned):
        return "правильно"
    if re.search(r"^[А-ЯA-Z0-9]+[vV]$", cleaned):
        return "правильно"
    if re.search(r"\s+(?:[xXхХ])$", cleaned) or re.search(r"^[А-ЯA-Z0-9]+[xXхХ]$", cleaned):
        return "неправильно"
    return None


def starts_marked_option(text: str) -> bool:
    return bool(re.search(r"^\s*(?:[VvY▼✓]|ч/)\s+", clean_line(text)))


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
        if gap > 58:
            break

        text = lines[line_index]["text"]
        if any(
            marker in text
            for marker in ["Тренировочный", "фармацевтическим", "Врач общей", "начало2022"]
        ):
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
    if correct_index is None:
        raise ValueError("correct answer not found")

    answer_lines = []
    for line in lines[instruction_index + 1 : end_index]:
        mark = visual_mark(line["text"])
        starts_option = starts_marked_option(line["text"])
        text = remove_answer_marks(line["text"])
        if text and not INSTRUCTION_RE.search(text) and not CHECK_RE.search(text):
            answer_lines.append(
                {
                    "y": line["y"],
                    "text": text,
                    "visual_mark": mark,
                    "starts_option": starts_option,
                }
            )

    answers = split_answers(answer_lines)
    answer_texts = [answer["text"] for answer in answers]

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


def apply_manual_fixes(question: dict) -> dict:
    number = question["question_number"]

    if number == 151:
        question["answers"] = [
            {"text": "Б", "mark": "неправильно"},
            {"text": "Г", "mark": "неправильно"},
            {"text": "А", "mark": "правильно"},
            {"text": "В", "mark": "неправильно"},
        ]

    if number == 219:
        question["answers"] = [
            {"text": "II, III, aVF", "mark": "правильно"},
            {"text": "V1, V2", "mark": "правильно"},
            {"text": "II, III, aVF, V1-3", "mark": "неправильно"},
            {"text": "V3, V4", "mark": "неправильно"},
            {"text": "I, aVL, V5-6", "mark": "неправильно"},
        ]

    if number == 417:
        question["answers"] = [
            {"text": "4", "mark": "неправильно"},
            {"text": "2", "mark": "правильно"},
            {"text": "3", "mark": "неправильно"},
            {"text": "1", "mark": "неправильно"},
        ]

    return question


def main() -> None:
    reader = PdfReader(str(PDF_PATH))
    first_ten = json.loads(FIRST_TEN_PATH.read_text(encoding="utf-8"))

    questions = []
    for page_index in range(len(reader.pages)):
        if page_index < len(first_ten):
            question = first_ten[page_index]
        else:
            question = extract_page(reader, page_index)

        questions.append(apply_manual_fixes(question))

    OUTPUT_PATH.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"written: {OUTPUT_PATH}")
    print(f"questions: {len(questions)}")


if __name__ == "__main__":
    main()
