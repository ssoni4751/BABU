"""
BABU Test Suite Package (tests/)
"""
import os
import sys

# Ensure root repository directory and babu package are in sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)
BABU_DIR = os.path.join(PARENT_DIR, "babu")
if BABU_DIR not in sys.path:
    sys.path.insert(1, BABU_DIR)
