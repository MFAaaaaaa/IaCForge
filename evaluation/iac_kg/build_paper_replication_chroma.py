#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
import chromadb


COLLECTION_RESOURCES = "terraform_resources"
COLLECTION_DOC_CHUNKS = "terraform_doc_chunks"
COLLECTION_EXAMPLES = "terraform_examples"
COLLECTION_OPTIONAL = "terraform_arguments_blocks"
EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"


def default_root():
    value = os.environ.get("IAC_KG_PAPER_REPLICATION_ROOT", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "data" / "paper_kg" / "source").resolve()


def default_index_dir(root):
    value = os.environ.get("IAC_KG_PAPER_CHROMA_DIR", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "data" / "paper_kg" / "chroma").resolve()


def docs_dir(root):
    return root / "notebooks_kg_construction" / "terraform_json_docs_with_summaries"


def parsed_docs_dir(root):
    return root / "data" / "tf_docs" / "parsed"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def split_text(text, chunk_size=1500, chunk_overlap=150):
    chunks = []
    start = 0
    length = len(text)
    separators = ["\n## ", "\n### ", "\n\n", "\n", ". ", " "]
    while start < length:
        end = min(length, start + chunk_size)
        if end < length:
            split_at = -1
            window = text[start:end]
            for separator in separators:
                idx = window.rfind(separator)
                if idx > max(200, chunk_size // 2):
                    split_at = start + idx + len(separator)
                    break
            if split_at > start:
                end = split_at
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(end - chunk_overlap, start + 1)
    return chunks


def traverse_blocks(blocks, parent_path, resource_name, source):
    docs = []
    for block in blocks or []:
        block_name = block.get("name", "unknown_block")
        full_path = f"{parent_path}.{block_name}" if parent_path else block_name
        summary = block.get("llm_summary") or block.get("description") or ""
        if summary:
            docs.append(
                Document(
                    page_content=f"{resource_name} {full_path}: {summary}",
                    metadata={
                        "source": source,
                        "type": "block",
                        "path": full_path,
                        "resource_name": resource_name,
                        "resource_type": resource_name,
                    },
                )
            )
        for arg in block.get("arguments", []) or []:
            summary = arg.get("llm_summary") or arg.get("description") or ""
            if summary:
                arg_path = f"{full_path}.{arg.get('name', '')}"
                docs.append(
                    Document(
                        page_content=f"{resource_name} {arg_path}: {summary}",
                        metadata={
                            "source": source,
                            "type": "nested_argument",
                            "path": arg_path,
                            "name": arg.get("name", ""),
                            "resource_name": resource_name,
                            "resource_type": resource_name,
                        },
                    )
                )
        docs.extend(traverse_blocks(block.get("blocks") or [], full_path, resource_name, source))
    return docs


def build_resource_docs(source_dir):
    docs = []
    for path in sorted(source_dir.glob("*.json")):
        data = read_json(path)
        resource = data.get("resource", {})
        resource_name = resource.get("name") or path.stem
        summary = resource.get("llm_summary") or resource.get("description") or ""
        if summary:
            docs.append(
                Document(
                    page_content=f"{resource_name}: {summary}",
                    metadata={
                        "source": path.name,
                        "type": "resource",
                        "name": resource_name,
                        "resource_name": resource_name,
                        "resource_type": resource_name,
                    },
                )
            )
        for arg in data.get("arguments", []) or []:
            summary = arg.get("llm_summary") or arg.get("description") or ""
            if summary:
                docs.append(
                    Document(
                        page_content=f"{resource_name} {arg.get('name', '')}: {summary}",
                        metadata={
                            "source": path.name,
                            "type": "argument",
                            "name": arg.get("name", ""),
                            "resource_name": resource_name,
                            "resource_type": resource_name,
                        },
                    )
                )
        for example in data.get("examples", []) or []:
            summary = example.get("llm_summary") or example.get("title") or ""
            if summary:
                docs.append(
                    Document(
                        page_content=f"{resource_name} {example.get('title', '')}: {summary}",
                        metadata={
                            "source": path.name,
                            "type": "example",
                            "title": example.get("title", ""),
                            "resource_name": resource_name,
                            "resource_type": resource_name,
                        },
                    )
                )
        docs.extend(traverse_blocks(data.get("blocks") or [], "", resource_name, path.name))
    return docs


def build_parsed_doc_chunks(source_dir):
    docs = []
    for path in sorted(source_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8", errors="ignore")
        resource_type = None
        match = re.search(r"# Resource: (aws_[^\n]+)", content)
        if match:
            resource_type = match.group(1).strip()
        else:
            match = re.search(r"(aws_[a-z0-9_]+)", path.name)
            if match:
                resource_type = match.group(1)
        if not resource_type:
            continue
        chunks = split_text(content)
        for index, chunk_text in enumerate(chunks):
            if not chunk_text.startswith(f"Resource: {resource_type}"):
                chunk_text = f"Resource: {resource_type}\n\n{chunk_text}"
            docs.append(
                Document(
                    page_content=chunk_text,
                    metadata={
                        "source": path.name,
                        "chunk_index": index,
                        "total_chunks": len(chunks),
                        "resource_type": resource_type,
                        "resource_name": resource_type,
                    },
                )
            )
    return docs


def build_example_docs(source_dir):
    docs = []
    for path in sorted(source_dir.glob("*.json")):
        data = read_json(path)
        resource_name = data.get("resource", {}).get("name") or path.stem
        for example in data.get("examples", []) or []:
            summary = example.get("llm_summary") or example.get("title") or ""
            if summary:
                docs.append(
                    Document(
                        page_content=f"{example.get('title', '')}\n{summary}",
                        metadata={
                            "resource": resource_name,
                            "resource_name": resource_name,
                            "resource_type": resource_name,
                            "title": example.get("title", ""),
                        },
                    )
                )
    return docs


def process_block(block, resource_name, parent_block_path=""):
    docs = []
    block_path = f"{parent_block_path}.{block['name']}" if parent_block_path else block["name"]
    summary = block.get("llm_summary") or block.get("description") or ""
    if summary:
        docs.append(
            Document(
                page_content=f"Block: {resource_name}.{block_path}\nDescription: {summary}",
                metadata={
                    "resource": resource_name,
                    "resource_name": resource_name,
                    "resource_type": resource_name,
                    "type": "block",
                    "path": block_path,
                },
            )
        )
    for arg in block.get("arguments", []) or []:
        summary = arg.get("llm_summary") or arg.get("description") or ""
        if summary:
            docs.append(
                Document(
                    page_content=f"Argument: {resource_name}.{block_path}.{arg.get('name', '')}\nDescription: {summary}",
                    metadata={
                        "resource": resource_name,
                        "resource_name": resource_name,
                        "resource_type": resource_name,
                        "type": "argument",
                        "path": f"{block_path}.{arg.get('name', '')}",
                    },
                )
            )
    for nested_block in block.get("blocks", []) or []:
        docs.extend(process_block(nested_block, resource_name, block_path))
    return docs


def build_optional_docs(source_dir):
    docs = []
    for path in sorted(source_dir.glob("*.json")):
        data = read_json(path)
        resource_name = data.get("resource", {}).get("name") or path.stem
        for arg in data.get("arguments", []) or []:
            if arg.get("required"):
                continue
            summary = arg.get("llm_summary") or arg.get("description") or ""
            if summary:
                docs.append(
                    Document(
                        page_content=f"{resource_name} {arg.get('name', '')}\n{summary}",
                        metadata={
                            "resource": resource_name,
                            "resource_name": resource_name,
                            "resource_type": resource_name,
                            "type": "argument",
                            "path": arg.get("name", ""),
                        },
                    )
                )
        for block in data.get("blocks", []) or []:
            cardinality = block.get("cardinality") or [0, 0]
            if cardinality and cardinality[0] == 0:
                docs.extend(process_block(block, resource_name))
    return docs


def main():
    root = default_root()
    source_dir = docs_dir(root)
    parsed_source_dir = parsed_docs_dir(root)
    index_dir = default_index_dir(root)
    if not source_dir.exists():
        raise SystemExit(f"Missing source dir: {source_dir}")
    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir))
    for collection in [
        COLLECTION_RESOURCES,
        COLLECTION_DOC_CHUNKS,
        COLLECTION_EXAMPLES,
        COLLECTION_OPTIONAL,
    ]:
        try:
            client.delete_collection(collection)
        except Exception:
            pass
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"local_files_only": True},
    )

    resource_docs = build_resource_docs(source_dir)
    Chroma.from_documents(
        documents=resource_docs,
        embedding=embeddings,
        persist_directory=str(index_dir),
        collection_name=COLLECTION_RESOURCES,
    )
    print(f"indexed {len(resource_docs)} resource/entity docs")

    if parsed_source_dir.exists():
        parsed_doc_chunks = build_parsed_doc_chunks(parsed_source_dir)
        if parsed_doc_chunks:
            Chroma.from_documents(
                documents=parsed_doc_chunks,
                embedding=embeddings,
                persist_directory=str(index_dir),
                collection_name=COLLECTION_DOC_CHUNKS,
            )
        print(f"indexed {len(parsed_doc_chunks)} parsed Terraform doc chunks")
    else:
        print(f"missing parsed Terraform docs dir: {parsed_source_dir}")

    example_docs = build_example_docs(source_dir)
    if example_docs:
        Chroma.from_documents(
            documents=example_docs,
            embedding=embeddings,
            persist_directory=str(index_dir),
            collection_name=COLLECTION_EXAMPLES,
        )
    print(f"indexed {len(example_docs)} example docs")

    optional_docs = build_optional_docs(source_dir)
    if optional_docs:
        Chroma.from_documents(
            documents=optional_docs,
            embedding=embeddings,
            persist_directory=str(index_dir),
            collection_name=COLLECTION_OPTIONAL,
        )
    print(f"indexed {len(optional_docs)} optional argument/block docs")
    print(index_dir)


if __name__ == "__main__":
    main()
