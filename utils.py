import json
import os


def clean_text(raw_text):
    if raw_text is None:
        return ""

    if not isinstance(raw_text, str):
        return raw_text

    try:
        return raw_text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw_text


def save_records(file_path, records):
    """Atomically write records (list) to JSON file."""
    temporary_path = file_path + ".tmp"

    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(list(records.values()), f, indent=2, ensure_ascii=False)

    os.replace(temporary_path, file_path)
