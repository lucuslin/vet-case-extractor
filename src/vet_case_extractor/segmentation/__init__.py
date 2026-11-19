"""Document segmentation into clinical case units (journal articles and scanned textbooks)."""

from vet_case_extractor.segmentation.book import segment_book_document
from vet_case_extractor.segmentation.indexer import build_project_index
from vet_case_extractor.segmentation.journal import segment_journal_document

__all__ = [
    "build_project_index",
    "segment_book_document",
    "segment_journal_document",
]
