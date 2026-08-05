# -*- coding: utf-8 -*-
"""Compiled game data for raw_shop, embedded in this module.

Placeholder
shop.json (the cash-shop / early-access catalog). When present, DATA is
``{"shop": [ {id, ...}, ... ]}`` and the codex Shop filter lists every
entry; until then ``DATA`` stays ``None`` and the filter falls back to the
known-premium id pattern (see codex.is_shop_item).
"""
DATA = None
