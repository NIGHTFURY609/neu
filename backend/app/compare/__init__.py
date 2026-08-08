"""Comparing two documents — versions of one contract, or two unrelated ones.

One engine, two pairings. The difference between "v1 vs v2 of our MSA" and "our MSA vs
theirs" is which pair of ids you hand it and how the result is labelled; the alignment,
diffing and delta logic is identical.

    assemble.py   chunks -> whole clauses (prerequisite for everything else here)
    align.py      pairing clauses across two documents
    textdiff.py   word-level diff of one aligned pair
    schemas.py    the wire contract
    service.py    putting the four together against the database
    routes.py     GET /compare
"""
