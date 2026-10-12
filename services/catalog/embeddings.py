"""Semantic encoding with a real multilingual ONNX model; no silent fallback."""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONS = 384


def cosine(left, right):
    a = sum(float(x) * float(y) for x, y in zip(left, right))
    b = math.sqrt(sum(float(x) ** 2 for x in left) * sum(float(y) ** 2 for y in right))
    return a / b if b else 0.0


class FastEmbedEncoder:
    """Lazy initialization lets health and document imports work while downloading."""
    model_name = DEFAULT_MODEL

    def __init__(self):
        self.model_name = os.getenv("CATALOG_EMBEDDING_MODEL", DEFAULT_MODEL)
        self._model = None
        self._lock = threading.RLock()

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                supported = {x["model"]: x for x in TextEmbedding.list_supported_models()}
                if self.model_name not in supported or supported[self.model_name]["dim"] != DIMENSIONS:
                    raise RuntimeError("Configure a supported 384-dimensional FastEmbed model")
                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=os.getenv("CATALOG_MODEL_CACHE", "/tmp/gastrogid-models"),
                    threads=int(os.getenv("CATALOG_EMBEDDING_THREADS", "2")),
                )
            return [[float(x) for x in vector] for vector in self._model.embed(texts, batch_size=32)]


class TestEncoder:
    """Nonsemantic fixture encoder. Never chosen implicitly or used for production."""
    model_name = "TEST-ONLY-token-hash-384"

    def encode(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * DIMENSIONS
            for token in re.findall(r"\w+", text.casefold()):
                index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % DIMENSIONS
                vector[index] += 1
            norm = math.sqrt(sum(x*x for x in vector)) or 1
            vectors.append([x/norm for x in vector])
        return vectors


def configured_encoder():
    provider = os.getenv("CATALOG_EMBEDDING_PROVIDER", "fastembed")
    if provider == "fastembed":
        return FastEmbedEncoder()
    if provider == "test" and os.getenv("CATALOG_TEST_MODE") == "1" and os.getenv("APP_ENV") != "production":
        return TestEncoder()
    raise RuntimeError("Invalid embedding provider: test encoder requires explicit CATALOG_TEST_MODE=1")


def menu_document(item, evidence_text=""):
    # Exclude synthetic metadata disclaimers from food embeddings.
    description = "" if getattr(item, "is_demo", False) else item.description or ""
    return "\n".join(filter(None, [
        item.name, description, " ".join(item.ingredients or []),
        " ".join(item.tags or []), evidence_text[:2000],
    ]))
