"""Independent Ruby Marshal 4.8 decoder for tests (follows Ruby's r_entry order).

Written from Ruby's marshal.c rules, NOT with rubymarshal, so tests can check
the app's .rvdata2 output without trusting the library under test.  Decodes
into plain Python trees with links resolved, so two byte strings can be
compared structurally: ``plain(load(a)) == plain(load(b))``.

Checked against all 414 data files of a real VX Ace game (every file decodes
completely).  Floats compare by their text; use ``float_values`` to compare
by value instead.
"""
import struct
import sys


class Bad(Exception):
    pass


class Obj:
    __slots__ = ("kind", "cls", "body")

    def __init__(self, kind, cls, body):
        self.kind, self.cls, self.body = kind, cls, body


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 2
        if data[:2] != b"\x04\x08":
            raise Bad("bad version")
        self.syms = []
        self.objs = []

    def byte(self):
        b = self.d[self.p]
        self.p += 1
        return b

    def long(self):
        c = struct.unpack("b", bytes([self.byte()]))[0]
        if c == 0:
            return 0
        if 4 < c < 128:
            return c - 5
        if -129 < c < -4:
            return c + 5
        if c > 0:
            x = 0
            for i in range(c):
                x |= self.byte() << (8 * i)
            return x
        c = -c
        x = -1
        for i in range(c):
            x &= ~(0xFF << (8 * i))
            x |= self.byte() << (8 * i)
        return x

    def bytes_(self):
        n = self.long()
        if n < 0 or self.p + n > len(self.d):
            raise Bad(f"bad length {n} at {self.p}")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def symbol(self):
        t = chr(self.byte())
        if t == ":":
            s = self.bytes_().decode("utf-8", "surrogateescape")
            self.syms.append(s)
            return s
        if t == ";":
            i = self.long()
            if not 0 <= i < len(self.syms):
                raise Bad(f"bad symlink {i}")
            return self.syms[i]
        if t == "I":   # symbol with encoding ivars
            s = self.symbol()
            for _ in range(self.long()):
                self.symbol()
                self.read()
            return s
        raise Bad(f"expected symbol, got {t!r} at {self.p - 1}")

    def entry(self, o):
        self.objs.append(o)
        return len(self.objs) - 1

    def read(self):
        t = chr(self.byte())
        if t == "0":
            return None
        if t == "T":
            return True
        if t == "F":
            return False
        if t == "i":
            return self.long()
        if t in ":;":
            self.p -= 1
            return ("sym", self.symbol())
        if t == "@":
            i = self.long()
            if not 0 <= i < len(self.objs):
                raise Bad(f"bad object link {i} (have {len(self.objs)}) at {self.p}")
            return self.objs[i]
        if t == "I":
            o = self.read()
            n = self.long()
            ivars = {}
            for _ in range(n):
                k = self.symbol()
                ivars[k] = self.read()
            if isinstance(o, Obj) and o.kind == "str":
                o.body = (o.body[0], ivars)
            return o
        if t == '"':
            o = Obj("str", None, None)
            self.entry(o)
            o.body = (self.bytes_(), {})
            return o
        if t == "f":
            o = Obj("float", None, self.bytes_())
            self.entry(o)
            return o
        if t == "l":
            sign = chr(self.byte())
            n = self.long()
            o = Obj("big", sign, self.d[self.p:self.p + 2 * n])
            self.p += 2 * n
            self.entry(o)
            return o
        if t == "[":
            o = Obj("arr", None, [])
            self.entry(o)
            for _ in range(self.long()):
                o.body.append(self.read())
            return o
        if t in "{}":
            o = Obj("hash", None, [])
            self.entry(o)
            for _ in range(self.long()):
                k = self.read()
                v = self.read()
                o.body.append((k, v))
            if t == "}":
                o.cls = self.read()
            return o
        if t == "o":
            o = Obj("obj", None, {})
            o.cls = self.symbol()
            self.entry(o)
            for _ in range(self.long()):
                k = self.symbol()
                o.body[k] = self.read()
            return o
        if t == "S":
            o = Obj("struct", None, {})
            o.cls = self.symbol()
            self.entry(o)
            for _ in range(self.long()):
                k = self.symbol()
                o.body[k] = self.read()
            return o
        if t == "u":
            cls = self.symbol()
            o = Obj("userdef", cls, self.bytes_())
            self.entry(o)
            return o
        if t == "U":
            o = Obj("usrmarshal", None, None)
            o.cls = self.symbol()
            self.entry(o)
            o.body = self.read()
            return o
        if t in "em":
            if t == "m":
                o = Obj("module", None, self.bytes_())
                self.entry(o)
                return o
            self.symbol()
            return self.read()
        if t == "C":
            cls = self.symbol()
            o = self.read()
            return o
        if t == "c":
            o = Obj("class", None, self.bytes_())
            self.entry(o)
            return o
        if t == "/":
            src = self.bytes_()
            opt = self.byte()
            o = Obj("regexp", opt, src)
            self.entry(o)
            return o
        raise Bad(f"unknown type {t!r} at {self.p - 1}")


def load(data):
    r = Reader(data)
    v = r.read()
    if r.p != len(r.d):
        raise Bad(f"trailing bytes: stopped at {r.p} of {len(r.d)}")
    return v


def plain(v, memo=None, depth=0):
    """Convert to comparable plain data (cycle-safe)."""
    if memo is None:
        memo = {}
    if not isinstance(v, Obj):
        return v
    if id(v) in memo:
        return ("<cycle>",)
    memo[id(v)] = True
    try:
        if v.kind == "str":
            return ("str", v.body[0])
        if v.kind in ("float", "big", "userdef", "class", "module", "regexp"):
            return (v.kind, v.cls, v.body)
        if v.kind == "arr":
            return ("arr", tuple(plain(x, memo, depth + 1) for x in v.body))
        if v.kind == "hash":
            return ("hash", tuple((plain(k, memo, depth + 1), plain(x, memo, depth + 1))
                                  for k, x in v.body), plain(v.cls, memo, depth + 1))
        if v.kind in ("obj", "struct"):
            return (v.kind, v.cls, tuple(sorted((k, plain(x, memo, depth + 1))
                                                for k, x in v.body.items())))
        if v.kind == "usrmarshal":
            return ("U", v.cls, plain(v.body, memo, depth + 1))
    finally:
        del memo[id(v)]
    return ("?", v.kind)


def float_values(t):
    """Same tree with Float text replaced by its value (0.8 == 0.800...04)."""
    if isinstance(t, tuple):
        if len(t) == 3 and t[0] == "float":
            return ("float", float(t[2]))
        return tuple(float_values(x) for x in t)
    return t


def tree(path):
    with open(path, "rb") as f:
        return plain(load(f.read()))


def first_diff(a, b, path="$"):
    if type(a) is not type(b):
        return f"{path}: type {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, tuple):
        if len(a) != len(b):
            return f"{path}: len {len(a)} vs {len(b)} ({str(a)[:80]} | {str(b)[:80]})"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {str(a)[:80]!r} vs {str(b)[:80]!r}"
