"""通用递归切分。"""

from langchain_text_splitters import RecursiveCharacterTextSplitter


class RecursiveSplitter:
    def __init__(self, chunk_size: int = 1200, chunk_overlap: int = 150):
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n## ", "\n# ", "\n\n", "\n", "。", "；", " ", ""],
        )

    def split(self, documents):
        return self._splitter.split_documents(documents)
