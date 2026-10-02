"""Opt-in experiment: retain every Line but detach unused blocks from Canvas."""

import math

from kivy.graphics import InstructionGroup


CHUNK = 64


def install(widget_class):
    build = widget_class._build_dynamic_canvas
    draw = widget_class._draw_stream

    def build_chunks(self):
        build(self)
        self._flow_chunks = {}
        for key, (group, _color, pool) in self._stream_pools.items():
            chunks = []
            for start in range(0, len(pool), CHUNK):
                chunk = InstructionGroup()
                for line in pool[start:start + CHUNK]:
                    group.remove(line)
                    chunk.add(line)
                chunks.append(chunk)
            self._flow_chunks[key] = [chunks, len(pool), 0]

    def draw_chunks(self):
        draw(self)
        for key, (group, _color, pool) in self._stream_pools.items():
            chunks, reserved, attached = self._flow_chunks[key]
            for index in range(reserved, len(pool)):
                if index % CHUNK == 0:
                    chunks.append(InstructionGroup())
                line = pool[index]
                group.remove(line)
                chunks[index // CHUNK].add(line)
            needed = math.ceil(self._stream_counts[key] / CHUNK)
            for chunk in chunks[needed:attached]:
                group.remove(chunk)
            for chunk in chunks[attached:needed]:
                group.add(chunk)
            self._flow_chunks[key][1:] = len(pool), needed

    widget_class._build_dynamic_canvas = build_chunks
    widget_class._draw_stream = draw_chunks
    widget_class.flow_renderer = "line_pool_chunks"
