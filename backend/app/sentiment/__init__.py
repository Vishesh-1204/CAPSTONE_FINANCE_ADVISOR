# app/sentiment/__init__.py
#
# Purpose: headline sentiment scorers (Phase 7A-4): base.py (contract), lexicon_scorer.py
# (default + baseline), finbert_scorer.py (optional, lazy), factory.py (choose + fall back).
# Nothing heavy is imported here: importing app.sentiment never loads torch or transformers.
