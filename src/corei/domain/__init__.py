"""Pure domain rules: units, time, identity and state machines.

Nothing in this package may read the wall clock, touch the network, or use
binary floats for financial values. ``tests/unit/test_domain_purity.py``
enforces this statically.
"""
