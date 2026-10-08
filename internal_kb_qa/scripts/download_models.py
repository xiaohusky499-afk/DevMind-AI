"""下载/复制所需模型到 internal_kb_qa/models 对应目录。

优先级：
1. 本地模型缓存 -> 直接复制
2. HuggingFace -> 下载
3. ModelScope -> 下载

用法：
    python -m internal_kb_qa.scripts.download_models bge-reranker-v2-m3
    python -m internal_kb_qa.scripts.download_models all
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

MODELS = {
    "bge-reranker-v2-m3": {
        "repo_id": "BAAI/bge-reranker-v2-m3",
        "required": ("config.json", "model.safetensors", "tokenizer.json", "sentencepiece.bpe.model"),
    },
    "bge-m3": {
        "repo_id": "BAAI/bge-m3",
        "required": ("config.json", "pytorch_model.bin", "sentencepiece.bpe.model"),
    },
}

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
LOCAL_MODELS_DIR = Path(__file__).resolve().parents[2] / "rag_qa" / "models"


def copy_from_reference(name: str, target: Path) -> bool:
    source = LOCAL_MODELS_DIR / name
    if not source.exists():
        return False
    required = MODELS[name]["required"]
    if not all((source / filename).exists() for filename in required):
        return False

    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"[OK] 已从本地模型缓存复制模型: {source} -> {target}")
    return True


def download_from_huggingface(name: str, target: Path) -> bool:
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=MODELS[name]["repo_id"],
            local_dir=str(target),
            local_dir_use_symlinks=False,
        )
        print(f"[OK] 已从 HuggingFace 下载模型: {MODELS[name]['repo_id']} -> {target}")
        return True
    except Exception as exc:
        print(f"[WARN] HuggingFace 下载失败: {exc}", file=sys.stderr)
        return False


def download_from_modelscope(name: str, target: Path) -> bool:
    try:
        from modelscope import snapshot_download

        snapshot_download(
            model_id=MODELS[name]["repo_id"],
            local_dir=str(target),
        )
        print(f"[OK] 已从 ModelScope 下载模型: {MODELS[name]['repo_id']} -> {target}")
        return True
    except Exception as exc:
        print(f"[WARN] ModelScope 下载失败: {exc}", file=sys.stderr)
        return False


def check_model(name: str, target: Path) -> bool:
    missing = [f for f in MODELS[name]["required"] if not (target / f).exists()]
    if missing:
        print(f"[FAIL] 模型文件不完整: {target}\n缺少: {missing}", file=sys.stderr)
        return False
    print(f"[OK] 模型就绪: {target}")
    return True


def download_one(name: str) -> bool:
    if name not in MODELS:
        print(f"未知模型: {name}，可选值: {list(MODELS)}", file=sys.stderr)
        return False

    target = MODELS_DIR / name
    target.mkdir(parents=True, exist_ok=True)

    if check_model(name, target):
        return True
    if copy_from_reference(name, target) and check_model(name, target):
        return True
    if download_from_huggingface(name, target) and check_model(name, target):
        return True
    if download_from_modelscope(name, target) and check_model(name, target):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="下载或复制 RAG 所需模型")
    parser.add_argument(
        "models",
        nargs="*",
        default=["bge-reranker-v2-m3"],
        help="模型名，如 bge-reranker-v2-m3；填 all 表示全部",
    )
    args = parser.parse_args()

    if args.models == ["all"]:
        names = list(MODELS)
    else:
        names = args.models

    failed = [name for name in names if not download_one(name)]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
