"""Ruby Marshal (.rvdata2) reading and writing for RPG Maker VX Ace.

``loads`` is rubymarshal's reader.  ``writes`` wraps rubymarshal's writer
so the object table matches Ruby's (see ``_RubyObjectTableWriter``); every
module that writes .rvdata2 files must use it, never rubymarshal directly.
"""

from rubymarshal.constants import TYPE_BIGNUM as _TYPE_BIGNUM
from rubymarshal.reader import loads
from rubymarshal.writer import Writer as _RubymarshalWriter
from rubymarshal.writer import writes as _rubymarshal_writes

__all__ = ["loads", "writes"]


class _RubyObjectTableWriter(_RubymarshalWriter):
    """rubymarshal's writer, numbering objects the way Ruby's Marshal does.

    Ruby gives every Float, String, Bignum and Regexp it writes a slot in
    the object table, and ``@n`` links count those slots.  rubymarshal
    1.2.10 only numbers objects it can link to, so every link written after
    a Float -- or after a plain ``str``, which is what a translated string
    is -- pointed at the wrong object, and the game loaded scrambled event
    data.  Reserve the missing slots so links stay correct.
    """

    def _reserve_slot(self):
        n = len(self.objects)
        self.objects[("rmt-slot", n)] = n

    def write(self, obj):
        if isinstance(obj, bytes):
            # A bare String; RubyString goes through must_write instead.
            self._reserve_slot()
        super().write(obj)

    def write_string(self, obj):
        self._reserve_slot()
        super().write_string(obj)

    def write_float(self, obj):
        self._reserve_slot()
        super().write_float(obj)

    def write_int(self, obj):
        # RGSS3 is a 32-bit Ruby: a Fixnum holds 31 bits.  Bigger values
        # (e.g. System's @version_id) were saved as Bignum and must stay
        # one -- rubymarshal writes up to 40 bits as Fixnum, which a 32-bit
        # Ruby reads back as a different number.
        if -(1 << 30) <= obj < (1 << 30):
            super().write_int(obj)
            return
        self._reserve_slot()
        self.fd.write(_TYPE_BIGNUM)
        self.fd.write(b"-" if obj < 0 else b"+")
        obj = abs(obj)
        shorts = (obj.bit_length() + 15) // 16
        self.write_long(shorts)
        for _ in range(shorts):
            self.write_short(obj & 0xFFFF)
            obj >>= 16

    def write_regexp(self, obj):
        self._reserve_slot()
        super().write_regexp(obj)


def writes(obj) -> bytes:
    """Serialise *obj* to Ruby Marshal bytes with correct object links."""
    return _rubymarshal_writes(obj, cls=_RubyObjectTableWriter)
