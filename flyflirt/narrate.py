"""Plain-English narration of what a message did to the real circuit.

Every claim is derived from the live simulation state (input channel strengths, recruited
cell counts, the strongest hub/output types). The parameter -> input-population mapping
itself is a modelling choice and is disclosed as such in the UI.
"""
from __future__ import annotations

# channel -> (region label, population description, positive-phrase, negative-phrase)
CHANNEL_INFO = {
    "brake_m1": ("Antennal lobe brake (mAL_m1)", "mAL_m1",
                 "Warmth is releasing the fly's doubt brake", "Friction is tightening the fly's doubt brake"),
    "brake_m8": ("Antennal lobe brake (mAL_m8)", "mAL_m8",
                 "Mutual engagement is releasing the fly's second doubt brake", "Coldness is clamping the fly's second brake"),
    "banter": ("Auditory-vocal input (AVLP732m/733m)", "AVLP732m/AVLP733m",
               "Playful banter is lighting the song-listening inputs", "Flat delivery is leaving the banter inputs idle"),
    "attention": ("Visual attention (LC16)", "LC16",
                  "Curiosity is locking the visual attention cells onto their partner", "Disinterest is letting visual attention drift"),
    "confiding": ("Lateral horn (LH003m)", "LH003m",
                  "Personal sharing is reaching the lateral horn", "Guardedness is keeping the lateral horn quiet"),
    "vigor": ("Flange input (FLA001m)", "FLA001m",
              "High energy is driving the flange inputs", "Low energy is leaving the flange inputs slack"),
    "feedback": ("Ascending feedback (AN08B020)", "AN08B020",
                 "Call-and-response is echoing through ascending feedback", "Missed replies are silencing the feedback loop"),
}

CHANNEL_LEGEND = [
    {"channel": "brake_m1", "drivers": "warmth (opposed by tension)", "population": "mAL_m1", "cells": "GABAergic brake"},
    {"channel": "brake_m8", "drivers": "reciprocity (opposed by tension)", "population": "mAL_m8", "cells": "GABAergic brake"},
    {"channel": "banter", "drivers": "humor", "population": "AVLP732m + AVLP733m", "cells": "cholinergic input"},
    {"channel": "attention", "drivers": "curiosity", "population": "LC16 (visual)", "cells": "cholinergic input"},
    {"channel": "confiding", "drivers": "disclosure", "population": "LH003m", "cells": "cholinergic input"},
    {"channel": "vigor", "drivers": "energy", "population": "FLA001m", "cells": "cholinergic input"},
    {"channel": "feedback", "drivers": "reciprocity", "population": "AN08B020", "cells": "cholinergic input"},
]


def narrate(trace, conn) -> dict:
    """Return {region, plain, technical} for one processed message."""
    channel = trace.top_channel
    hub = next((t for t in trace.top_types if conn.types and _is_role(conn, t["type"], "accumulator")), None)
    out = next((t for t in trace.top_types if _is_role(conn, t["type"], "output")), None)

    if channel is None:
        region, plain = "Whole circuit", "A quiet message: the fly barely stirs."
    else:
        region, _pop, pos, neg = CHANNEL_INFO[channel]
        strength = trace.channels.get(channel, 0.0)
        plain = pos if strength > 0 else neg
        if hub:
            plain += f", and the pC1 hub cell type {hub['type']} is the first to respond."
        else:
            plain += "."

    if out and out["level"] > 0.02:
        plain += " The descending output cells are firing: that is the fly's decision signal."

    technical = (
        f"{trace.new_cells} neurons and {trace.new_edges} connections recruited for the first time "
        f"({trace.cells_active} / {trace.edges_active} active in total)"
    )
    if hub:
        technical += f" · strongest hub type {hub['type']} ({hub['level']:.2f})"
    return {"region": region, "plain": plain, "technical": technical}


def _is_role(conn, type_name: str, role: str) -> bool:
    try:
        t = conn.types.index(type_name)
    except ValueError:
        return False
    cells = conn.type_cells.get(t)
    return cells is not None and cells.size > 0 and conn.roles[int(conn.role[cells[0]])] == role
