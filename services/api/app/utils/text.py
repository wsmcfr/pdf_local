import re

import jieba


def tokenize_zh(text: str) -> str:
    """Split Chinese text with jieba and join tokens with spaces.

    FTS5 is configured with the ``unicode61`` tokenizer, which does not split
    CJK runs on its own. Pre-tokenizing with ``cut_for_search`` (which also
    emits partial words) keeps two-character terms such as "合同" searchable.
    """
    tokens = [
        token.strip()
        for token in jieba.cut_for_search(text)
        if token.strip() and any(char.isalnum() for char in token)
    ]
    return " ".join(tokens)


def normalize_text(text: str) -> str:
    """Collapse runs of spaces/tabs and trim, keeping paragraph breaks."""
    return re.sub(r"[ \t]+", " ", text).strip()
