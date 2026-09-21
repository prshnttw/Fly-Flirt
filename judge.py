import circuit_sim

OUTPUT_NODE = "pIP10"
QUIET_THRESHOLD = 0.05
OUTPUT_FIRE_THRESHOLD = 0.5


def judge_line(state):
    """
    A real-time readout of the actual simulated MaleCNS circuit — every id and
    number here comes straight from circuit_sim's live state and circuit.json's
    real synapse counts, not from reading the chat text. Returns a dict with
    a named brain region, a plain-English translation, and the underlying
    technical readout, so the same result can be shown at either level.
    """
    sensory_id, sensory_val = circuit_sim.top_node("sensory")
    accumulator_id, accumulator_val = circuit_sim.top_node("accumulator")
    output_id, output_val = circuit_sim.top_node("output")

    if state == "verdict" or output_val >= OUTPUT_FIRE_THRESHOLD:
        raw_w = circuit_sim.raw_synapse_count(accumulator_id, OUTPUT_NODE)
        verdict_phrase = "Verdict declared." if state == "verdict" else "Verdict incoming."
        return {
            "region": "Descending Output (pIP10)",
            "plain": "The fly has made up its mind — this is what's driving the compatibility verdict.",
            "technical": (
                f"{output_id} firing at {output_val:.2f} — {accumulator_id} alone "
                f"drives it through {raw_w} real synapses. {verdict_phrase}"
            ),
        }

    if abs(accumulator_val) >= abs(sensory_val) and abs(accumulator_val) >= QUIET_THRESHOLD:
        raw_w = circuit_sim.raw_synapse_count(accumulator_id, OUTPUT_NODE)
        return {
            "region": "Courtship Center (pC1)",
            "plain": "Arousal is building in the fly's courtship circuit — the chemistry is accumulating.",
            "technical": (
                f"{accumulator_id} leading the accumulator at {accumulator_val:.2f} — "
                f"{raw_w} real synapses run straight from here to {output_id}."
            ),
        }

    if abs(sensory_val) >= QUIET_THRESHOLD:
        return {
            "region": "Antennal Lobe (mAL) — Sensory Relay",
            "plain": "The fly's built-in doubt signal is quieting down, but the excitement hasn't caught up yet.",
            "technical": (
                f"{sensory_id} suppressed to {sensory_val:.2f} — inhibition lifting, "
                f"pC1 hasn't caught up yet."
            ),
        }

    return {
        "region": "Whole Circuit",
        "plain": "Nothing much is happening yet — the fly is still waiting to be impressed.",
        "technical": "Circuit's quiet — mAL, pC1, and pIP10 are all sitting near baseline.",
    }
