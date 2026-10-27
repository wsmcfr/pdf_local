from __future__ import annotations

from .citations import citation_label

SYSTEM_PROMPT = """你是一个本地知识库问答助手。

规则：
1. 只能使用下面提供的资料回答，不得使用外部知识。
2. 每个结论后必须标注来源：
   - PDF 资料使用 [文件名 p.页码]
   - Markdown/TXT 资料使用 [文件名 §章节]
3. 只能引用资料中实际出现过的文件名和页码/章节。
4. 如果资料不足以回答，只输出：资料中没有找到。
5. 不要编造文件名、页码、章节或数据。
6. 用中文回答，简洁、直接。
"""


def build_messages(question: str, contexts: list[dict]) -> list[dict]:
    context_text = "\n\n".join(
        f"[{citation_label(item['file_name'], item['page_no'], item.get('mime'))}]\n"
        f"{item['text']}"
        for item in contexts
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"资料：\n{context_text}\n\n问题：{question}",
        },
    ]
