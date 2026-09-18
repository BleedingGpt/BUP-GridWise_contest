SYSTEM_PROMPT = """You are an energy scheduling directive interpreter. Your job is to read natural-language operator notes and convert them into structured directives for a smart campus energy optimizer.

SUPPORTED DIRECTIVE TYPES:
1. solar_reduction - Reduce usable solar during specific hours
2. minimum_battery_reserve - Keep battery energy above a required level during specific hours
3. no_charge_window - Battery charging unavailable during specific hours
4. no_discharge_window - Battery discharging unavailable during specific hours
5. max_grid_window - Grid import capped at a maximum during specific hours
6. no_op - Note does not affect the current 24-hour energy schedule

RULES:
- Return exactly one directive per note, in the same order as input.
- Hours must be integers 0-23, ascending, no duplicates.
- For solar_reduction, factor is the REMAINING fraction (0.0 to 1.0). An 80% reduction = factor 0.2.
- Time windows are half-open: "1 PM to 3 PM" means hours [13, 14].
- If a note is irrelevant to energy scheduling, use no_op.
- Do NOT invent demand, tariff, or battery values.
- Do NOT use directive types not listed above.

OUTPUT FORMAT (JSON array):
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
    "explanation": "Brief explanation"
  }
]

For no_op entries:
  "applies": false,
  "directive_type": "no_op",
  "structured_adjustment": null

IMPORTANT: Return ONLY the JSON array. No markdown, no code fences, no extra text."""

FEW_SHOT_EXAMPLES = [
    {
        "notes": [
            "Solar output will drop to about 20% from 1 PM to 3 PM.",
            "Do not charge the battery between 2 PM and 4 PM.",
            "The cafeteria menu changes tomorrow."
        ],
        "expected": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
                "explanation": "Solar availability reduced during panel cleaning window."
            },
            {
                "note_index": 1,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": [14, 15]},
                "explanation": "Battery charging restricted during afternoon peak."
            },
            {
                "note_index": 2,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Cafeteria menu does not affect energy scheduling."
            }
        ]
    },
    {
        "notes": [
            "Keep at least 120 kWh in reserve from 6 PM until 9 PM.",
            "Grid import should not exceed 50 kWh between 8 AM and 10 AM."
        ],
        "expected": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 120.0},
                "explanation": "Battery reserve required during evening peak demand."
            },
            {
                "note_index": 1,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": [8, 9], "max_grid_kwh": 50.0},
                "explanation": "Grid import capped during morning peak."
            }
        ]
    },
    {
        "notes": [
            "Panel washing from one until three will leave roughly one-fifth of normal solar output.",
            "Don't discharge the battery during midnight to 5 AM."
        ],
        "expected": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
                "explanation": "Panel washing reduces solar to 20% of normal."
            },
            {
                "note_index": 1,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": [0, 1, 2, 3, 4]},
                "explanation": "Battery discharge restricted during off-peak night hours."
            }
        ]
    },
    {
        "notes": [
            "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
            "Team building event scheduled for conference room B."
        ],
        "expected": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
                "explanation": "80% solar reduction during maintenance = 20% remaining."
            },
            {
                "note_index": 1,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Team building event does not affect energy scheduling."
            }
        ]
    }
]


def build_interpretation_prompt(notes: list[str]) -> str:
    """Build the full prompt with few-shot examples for the LLM."""
    prompt = SYSTEM_PROMPT + "\n\n"
    prompt += "EXAMPLES:\n\n"
    for i, example in enumerate(FEW_SHOT_EXAMPLES, 1):
        prompt += f"Example {i}:\n"
        prompt += f"Input notes: {example['notes']}\n"
        prompt += f"Output: {example['expected']}\n\n"
    prompt += "NOW INTERPRET THESE NOTES:\n\n"
    prompt += f"Input notes: {notes}\n"
    prompt += "Output:"
    return prompt
