"""Reglas de tono/formato por canal. Persistidas en mock_crm.json bajo
'channel_rules_default'. Editable desde admin → reglas por canal.
"""
from __future__ import annotations
from typing import Any, Dict


def get_all(crm: Dict[str, Any]) -> Dict[str, Any]:
    return crm.get("channel_rules_default", {})


def get_one(crm: Dict[str, Any], channel: str) -> Dict[str, Any]:
    return get_all(crm).get(channel, {})


def update(crm: Dict[str, Any], channel: str, rule: Dict[str, Any]) -> Dict[str, Any]:
    rules = crm.setdefault("channel_rules_default", {})
    current = rules.get(channel, {})
    current.update(rule)
    rules[channel] = current
    return current
