"""Extract declared menu fields; preserve unsupported layouts for manual review."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import re
import subprocess
import tempfile

from bs4 import BeautifulSoup
from PIL import Image

from .schemas import AllergenDeclaration, DraftItem, NutritionFacts

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 30
MAX_TEXT = 300000
MAX_IMAGE_PIXELS = 25000000
PRICE = re.compile(r"(?<![\d.])(\d{1,7}(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|RUB)(?!\w)", re.I)
WEIGHT = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:г\.?|g)(?!\w)", re.I)


@dataclass
class Fragment:
    key: str
    text: str
    page: int | None


@dataclass
class Extraction:
    fragments: list[Fragment]
    items: list[dict]
    warnings: list[str]


def number(value):
    return float(Decimal(str(value).strip().replace(",", ".")))


def money(value):
    return int((Decimal(str(value).strip().replace(",", ".")) * 100).quantize(Decimal("1")))


def split_list(value):
    return [x.strip() for x in re.split(r"[,;]", value or "") if x.strip()]


def ocr_image(image: Image.Image):
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError("Image exceeds 25 megapixels")
    with tempfile.TemporaryDirectory(prefix="gastrogid-ocr-") as temp:
        path = Path(temp) / "page.png"
        image.convert("RGB").save(path)
        try:
            result = subprocess.run(
                ["tesseract", str(path), "stdout", "-l", "rus+eng", "--psm", "6"],
                capture_output=True, check=True, timeout=90,
            )
        except FileNotFoundError as exc:
            raise ValueError("OCR unavailable: install tesseract with rus and eng language data") from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError("OCR failed; verify tesseract rus+eng language data") from exc
        return result.stdout.decode("utf-8", errors="replace")


def validate_file(kind, content):
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Upload must be nonempty and at most 20 MiB")
    if kind == "pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF signature")
    elif kind == "image":
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("Only PNG, JPEG and WebP images are supported")
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Image exceeds 25 megapixels")
                image.verify()
        except Exception as exc:
            raise ValueError("Invalid or oversized PNG/JPEG/WebP image") from exc
    elif kind == "html":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("HTML must use UTF-8") from exc
        if not re.search(r"<(?:html|table|div|section|article|li|body|p|h[1-6])\b", text, re.I):
            raise ValueError("Upload does not contain HTML markup")
    else:
        raise ValueError("Unsupported source kind")


def line_item(text, key):
    """Only lines with an explicit RUB/₽ price are interpreted as menu rows."""
    match = PRICE.search(text)
    if not match:
        return None
    first = text.split("|")[0].strip()
    name = PRICE.sub("", first).strip(" -–—:;\t")
    name = WEIGHT.sub("", name).strip(" -–—:;\t")
    if not name or len(name) > 200:
        return None
    item = DraftItem(draft_key=key, name=name, price_minor=money(match.group(1)))
    weight = WEIGHT.search(text)
    if weight:
        item.portion_weight_g = number(weight.group(1))
    for part in text.split("|")[1:]:
        lowered = part.strip().casefold()
        if lowered.startswith(("состав:", "ingredients:")):
            item.ingredients = split_list(part.split(":", 1)[1])
        if lowered.startswith(("теги:", "tags:")):
            item.tags = split_list(part.split(":", 1)[1])
        if lowered.startswith(("полный состав:", "complete ingredients:")):
            item.ingredients = split_list(part.split(":", 1)[1])
            item.ingredients_complete = True
    # Text KБЖУ is accepted only when it explicitly says "на порцию".
    if re.search(r"(?:на\s+порцию|per\s+portion)", text, re.I):
        facts = {}
        labels = {"calories_kcal": r"(?:ккал|kcal)", "protein_g": r"(?:белки|protein)",
                  "fat_g": r"(?:жиры|fat)", "carbs_g": r"(?:углеводы|carbs)"}
        for field, label in labels.items():
            found = re.search(label + r"\s*[:=]\s*(\d+(?:[.,]\d+)?)", text, re.I)
            if found:
                facts[field] = number(found.group(1))
        # Evidence is attached by process_import before Pydantic validation.
        if facts:
            data = item.model_dump(mode="json")
            data["nutrition"].update(facts, source="restaurant_declared")
            return data
    return item.model_dump(mode="json")


def html_rows(content):
    soup = BeautifulSoup(content.decode("utf-8-sig"), "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    cards = soup.select('[data-menu-item], .menu-item, [itemtype$="/MenuItem"]')
    fragments, items = [], []
    if cards:
        for index, card in enumerate(cards[:500]):
            key = f"html-{index+1}"
            text = card.get_text(" | ", strip=True)
            attrs = {k: v for k, v in card.attrs.items() if k.startswith("data-")}
            # Preserve attributes too: they can contain the actual declaration.
            evidence = text + "\n" + "; ".join(f"{k}={v}" for k, v in attrs.items())
            fragments.append(Fragment(key, evidence, None))
            name = card.get("data-name")
            if not name:
                name_node = card.select_one('[itemprop="name"], .name, h2, h3, h4')
                name = name_node.get_text(" ", strip=True) if name_node else None
            row = line_item(text, key)
            if not name and row:
                name = row["name"]
            if not name:
                continue
            data = row or DraftItem(draft_key=key, name=name).model_dump(mode="json")
            data["name"] = str(name).strip()
            attr_fields = {"data-price": ("price_minor", money), "data-price-minor": ("price_minor", int),
                           "data-weight-g": ("portion_weight_g", number)}
            for attr, (field, convert) in attr_fields.items():
                if card.get(attr) is not None:
                    data[field] = convert(card[attr])
            for attr, field in [("data-ingredients", "ingredients"), ("data-tags", "tags")]:
                if card.get(attr) is not None:
                    data[field] = split_list(card[attr])
            data["ingredients_complete"] = card.get("data-ingredients-complete") == "true"
            if card.get("data-description"):
                data["description"] = card["data-description"]
            declarations = []
            for declaration in split_list(card.get("data-allergens")):
                code, sep, status = declaration.partition(":")
                if sep:
                    declarations.append(AllergenDeclaration(code=code.strip(), status=status.strip()).model_dump(mode="json"))
            if declarations:
                data["allergens"] = declarations
                data["allergen_info_status"] = "complete" if card.get("data-allergen-status") == "complete" else "partial"
            if card.get("data-nutrition-basis") == "per_portion":
                facts = {}
                for attr, field in [("data-kcal", "calories_kcal"), ("data-protein-g", "protein_g"),
                                    ("data-fat-g", "fat_g"), ("data-carbs-g", "carbs_g")]:
                    if card.get(attr) is not None:
                        facts[field] = number(card[attr])
                if facts:
                    data["nutrition"].update(facts, source="restaurant_declared")
            items.append(data)
    else:
        tables = soup.select("table tr")
        lines = [" | ".join(x.get_text(" ", strip=True) for x in row.select("td")) for row in tables]
        if not tables:
            lines = [x.strip() for x in soup.get_text("\n", strip=True).splitlines() if x.strip()]
        for index, text in enumerate(lines[:2000]):
            key = f"html-{index+1}"
            fragments.append(Fragment(key, text, None))
            row = line_item(text, key)
            if row:
                items.append(row)
    return fragments, items


def extract(kind, content):
    validate_file(kind, content)
    warnings = ["Автоматическое извлечение требует проверки администратором; отсутствующие данные не вычислялись."]
    if kind == "html":
        fragments, items = html_rows(content)
    else:
        pages = []
        if kind == "image":
            with Image.open(BytesIO(content)) as image:
                pages.append((1, ocr_image(image)))
            warnings.append("Текст распознан OCR: проверьте названия, числа и состав по оригиналу.")
        else:
            import fitz
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.is_encrypted:
                    raise ValueError("Encrypted PDFs are not supported")
                if len(document) > MAX_PAGES:
                    raise ValueError(f"PDF exceeds {MAX_PAGES} pages")
                for number_page, page in enumerate(document, 1):
                    text = page.get_text(sort=True)
                    if len(text.strip()) < 20:
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                        text = ocr_image(Image.open(BytesIO(pixmap.tobytes("png"))))
                        warnings.append(f"Страница {number_page} распознана OCR; проверьте по оригиналу.")
                    pages.append((number_page, text))
        if sum(len(text) for _, text in pages) > MAX_TEXT:
            raise ValueError("Extracted document text exceeds processing limit")
        fragments, items = [], []
        for page_number, text in pages:
            fragments.append(Fragment(f"page-{page_number}", text, page_number))
            for index, line in enumerate(text.splitlines()):
                key = f"page-{page_number}-line-{index+1}"
                row = line_item(line.strip(), key)
                if row:
                    fragments.append(Fragment(key, line.strip(), page_number))
                    items.append(row)
    if sum(len(x.text) for x in fragments) > MAX_TEXT:
        raise ValueError("Extracted document text exceeds processing limit")
    if len(items) > 500:
        raise ValueError("Document exceeds 500 menu rows; split the upload")
    if not items:
        warnings.append("Позиции не удалось выделить автоматически. Добавьте их в черновик по сохранённым фрагментам источника.")
    return Extraction(fragments, items, warnings)
