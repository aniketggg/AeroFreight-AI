#!/usr/bin/env python3
"""
Run the AeroFreight Settlement & Payment agent.

Usage (from this directory's parent):
    python3 -m aerofreight_settlement.run_agent
Or simply:
    python3 run_agent.py
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

if __name__ == "__main__":
    from settlement_agent import agent

    print("AeroFreight Settlement & Payment agent address:", agent.address)
    agent.run()
