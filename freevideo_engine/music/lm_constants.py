# ACE-Step 1.5 metadata constants (MIT).
"""
Constants for ACE-Step
Centralized constants used across the codebase
"""

# ==============================================================================
# Language Constants
# ==============================================================================

# Supported languages for vocal generation and language detection
# Covers major world languages with good TTS support in the underlying model
# 'unknown' is used when language cannot be determined automatically
VALID_LANGUAGES = [
    'ar', 'az', 'bg', 'bn', 'ca', 'cs', 'da', 'de', 'el', 'en',
    'es', 'fa', 'fi', 'fr', 'he', 'hi', 'hr', 'ht', 'hu', 'id',
    'is', 'it', 'ja', 'ko', 'la', 'lt', 'ms', 'ne', 'nl', 'no',
    'pa', 'pl', 'pt', 'ro', 'ru', 'sa', 'sk', 'sr', 'sv', 'sw',
    'ta', 'te', 'th', 'tl', 'tr', 'uk', 'ur', 'vi', 'yue', 'zh',
    'unknown'
]


# ==============================================================================
# Keyscale Constants
# ==============================================================================

# Musical note names using standard Western notation
KEYSCALE_NOTES = ['A', 'B', 'C', 'D', 'E', 'F', 'G']

# Supported accidentals: natural, ASCII sharp/flat, Unicode sharp/flat
KEYSCALE_ACCIDENTALS = ['', '#', 'b', '♯', '♭']  # empty + ASCII sharp/flat + Unicode sharp/flat

# Major and minor scale modes
KEYSCALE_MODES = ['major', 'minor']

# Generate all valid keyscales: 7 notes × 5 accidentals × 2 modes = 70 combinations
# Examples: "C major", "F# minor", "B♭ major"
VALID_KEYSCALES = set()
for note in KEYSCALE_NOTES:
    for acc in KEYSCALE_ACCIDENTALS:
        for mode in KEYSCALE_MODES:
            VALID_KEYSCALES.add(f"{note}{acc} {mode}")


# ==============================================================================
# Metadata Range Constants
# ==============================================================================

# BPM (Beats Per Minute) range - covers most musical styles
# 30 BPM: Very slow ballads, ambient music
# 300 BPM: Fast electronic dance music, extreme metal
BPM_MIN = 30
BPM_MAX = 300

# Duration range (in seconds) - balances quality vs. computational cost
# 10s: Short loops, musical excerpts
# 600s: Full songs, extended compositions (10 minutes)
DURATION_MIN = 10
DURATION_MAX = 600

# Valid time signatures - common musical meter patterns
# 2: 2/4 time (marches, polka)
# 3: 3/4 time (waltzes, ballads)
# 4: 4/4 time (most pop, rock, hip-hop)
# 6: 6/8 time (compound time, folk dances)
VALID_TIME_SIGNATURES = [2, 3, 4, 6]


# ==============================================================================

DEFAULT_LM_INSTRUCTION = "Generate audio semantic tokens based on the given conditions:"
