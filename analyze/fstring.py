"""Factorio 字符串编解码（兼容 helpers.encode_string / helpers.decode_string）。

格式: '0' + base64( zlib( utf8(json) ) )
第一个字符是版本字节; 0 表示无版本信息。
"""
from __future__ import annotations

import base64
import json
import zlib


def encode_string(text: str) -> str:
    """与游戏 helpers.encode_string 一致：纯 base64(zlib(utf8))，无版本前缀。"""
    raw = text.encode("utf-8")
    comp = zlib.compress(raw, 9)
    return base64.b64encode(comp).decode("ascii")


def decode_string(s: str) -> str:
    s = s.strip()
    if not s:
        raise ValueError("empty string")
    body = s[1:] if s[0] in "0123456789" else s
    return zlib.decompress(base64.b64decode(body)).decode("utf-8")


def encode_json(obj) -> str:
    return encode_string(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def decode_json(s: str):
    return json.loads(decode_string(s))
