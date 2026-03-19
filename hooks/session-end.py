#!/usr/bin/env python3
"""
SessionEnd Hook — Sem operação.
O salvamento real é feito pelo /recall-save antes de encerrar.
"""

import json
import sys

print(json.dumps({'continue': True, 'suppressOutput': True}))
