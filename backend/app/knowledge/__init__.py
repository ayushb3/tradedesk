"""knowledge base (PRD-M8). M8a: transcript ingestion, local embeddings,
ChromaDB (or numpy-fallback) vector index, and search.

Everything runtime-heavy (sentence-transformers, chromadb) is optional and lives
behind seams so the app boots without it — knowledge endpoints then report a
clear "not ingested / embeddings unavailable" state instead of crashing.
"""
