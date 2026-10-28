"""Specifications and Prompts for the 'Fem Malfoy: The Battle of Hogwarts' Narrative Scene.

A 10-clip suspense-comedy-dramatic continuous story featuring:
- Lord Voldemort targeting Hermione
- Harry attempting to shield her
- Female Malfoy taking the hit and turning into Male Draco
- Comical horror and Draco's furious demand: 'Turn me back... NOW!'
- Professor McGonagall's holy counter-curse
- Female Malfoy's joyful return, furious snap: 'How dare YOU!!!'
- Blinding overpowered apocalyptic finishing spell
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StoryClipSpec:
    clip_id: str
    scene_index: int
    title: str
    description: str
    prompt: str
    duration: float
    seed: int
    reference_image_file: Optional[str] = None
    width: int = 448
    height: int = 256


STORY_BIBLE: str = (
    "MASTER STORY BIBLE & VISUAL STAGING ANCHORS:\n"
    "[Setting & Atmosphere]: The shattered, wet stone courtyard of Hogwarts Castle at dark twilight during the final battle. "
    "Ancient ruined Gothic archways, smoking rubble, and crumbling stone flagstones under a menacing, churning sky of dark stormy clouds with distant flashes of magical fire. "
    "Wet ground reflects flickering torchlight and magical sparks. Cinematic realistic photography, dark fantasy color grading, volumetric smoke, high visual fidelity, photorealistic textures.\n"
    "[Spatial Staging & Camera Rules]: Fixed spatial orientation across all shots: "
    "Screen-LEFT is occupied by Lord Voldemort facing towards the right. "
    "Screen-RIGHT is occupied by Harry Potter, Hermione Granger, and Female Malfoy facing towards the left. "
    "Background / Center-Right is Professor McGonagall stepping forward between them. "
    "All shots default to steady cinematic eye-level medium shots (waist-up) or medium-wide ensemble shots. Strict rule: NO unsolicited extreme close-ups, NO warped fish-eye angles, NO sudden 180-degree camera flips.\n"
    "[Character Anchors]:\n"
    "- Female Malfoy (Lyra Malfoy): aristocratic pale porcelain skin, expressive piercing silver-grey eyes, long silky platinum-blonde hair cascading past shoulders, tailored green-and-silver Slytherin battle robes with dark pleated skirt.\n"
    "- Male Draco Malfoy: pointed pale face, sleek slicked-back platinum-blonde hair, silver eyes, wearing the exact same oversized Slytherin robes.\n"
    "- Lord Voldemort: deathly pale bald serpentine face, crimson slit pupils, snake nostrils, flowing billowing black robes, holding the Elder Wand.\n"
    "- Harry Potter: round dark-rimmed spectacles, tousled black hair, lightning scar, Gryffindor robes.\n"
    "- Hermione Granger: wavy bushy brown hair, determined intelligent face, Gryffindor uniform.\n"
    "- Professor Minerva McGonagall: severe dignified expression, emerald tartan travelling cloak, pointed witch hat, authoritative posture."
)


STORY_CLIPS: List[StoryClipSpec] = [
    StoryClipSpec(
        clip_id='scene01_voldemort_attack',
        scene_index=1,
        title='The Dark Lord Strikes',
        description='Lord Voldemort casts a lethal curse toward Hermione across the ruined courtyard.',
        prompt=(
            "[Shot 1] At 00:00.000, wide medium shot across the ruined courtyard. On screen-left, Lord Voldemort raises the Elder Wand, "
            "his serpentine face sneering with malicious triumph as he slashes his wand towards screen-right. "
            "[Shot 2] At 00:01.400, cut to medium shot of Hermione Granger cornered against stone arches on screen-right, "
            "as a crackling bolt of emerald-black dark lightning tears across the wet flagstones directly toward her.\n\n"
            "overall_soundscape: Howling mountain gale, violent crackle of dark lightning, Voldemort's chilling hiss-laugh, crumbling stone debris.\n\n"
            "non_diegetic_music: Ominous, driving battle percussion with menacing low brass and frantic high string ostinatos building instant panic."
        ),
        duration=2.8,
        seed=20261001,
    ),
    StoryClipSpec(
        clip_id='scene02_harry_and_fem_sacrifice',
        scene_index=2,
        title='The Sacrifice & Smoke Blast',
        description='Harry tries to shield Hermione, but Female Malfoy dashes in front and takes the direct hit.',
        prompt=(
            "[Shot 1] At 00:00.000, medium shot on screen-right: Harry Potter lunges forward to shield Hermione, shouting in alarm. "
            "[Shot 2] At 00:01.300, Female Malfoy (Lyra Malfoy) with flowing platinum-blonde hair dashes heroically from the right, "
            "throwing herself directly between Harry and the curse. The dark lightning strikes her chest squarely, "
            "exploding into a massive, dense vortex of purple and grey magical smoke that completely envelops her.\n\n"
            "overall_soundscape: Rushing footsteps on wet flagstones, Harry's muffled shout, deafening concussive magical blast, ringing aftermath.\n\n"
            "non_diegetic_music: Sudden dramatic orchestral hit swelling into a suspended, heart-stopping dramatic chord."
        ),
        duration=3.0,
        seed=20261002,
    ),
    StoryClipSpec(
        clip_id='scene03_smoke_clears_male_draco',
        scene_index=3,
        title='The Smoke Clears: Male Draco Malfoy',
        description='The purple smoke clears revealing Male Draco Malfoy. Harry and Hermione look dumbfounded.',
        prompt=(
            "[Shot 1] At 00:00.000, eye-level medium shot as the dense violet smoke billows away. Standing in her exact position on screen-right "
            "is Male Draco Malfoy, with slicked-back platinum hair, pale pointed chin, and comical oversized Slytherin robes. "
            "[Shot 2] At 00:02.200, cut to medium two-shot of Harry Potter and Hermione Granger on screen-right, completely frozen in dumbfounded disbelief, "
            "their eyes wide and jaws hanging open in utter bewilderment.\n\n"
            "overall_soundscape: Hissing smoke dissipating in the wind, dead silence, quiet stunned gasp from Hermione.\n\n"
            "non_diegetic_music: A sudden comical record-scratch freeze followed by a quirky, bewildered pizzicato string pluck."
        ),
        duration=4.5,
        seed=20261003,
    ),
    StoryClipSpec(
        clip_id='scene04_draco_identity_crisis',
        scene_index=4,
        title='Draco Realizes the Transformation',
        description='Draco touches his face and chest, horrified by the male transformation, escalating into rage.',
        prompt=(
            "[Shot 1] At 00:00.000, medium shot of Draco Malfoy on screen-right looking down in confusion at his hands. He frantically pats his flat chest, "
            "feels his sharp male jawline, and gasps in high-pitched masculine horror as he hears his own deep voice. "
            "[Shot 2] At 00:02.800, his silver eyes bug out in comical panic, his expression rapidly transforming from hysterical shock into trembling, "
            "indignant outrage, his cheeks flushing bright crimson.\n\n"
            "overall_soundscape: Frantic patting of robes, high-pitched male gasp, shaking breath, comedic squeak.\n\n"
            "non_diegetic_music: Humorous comedic bassoon flutter and rising tremolo violins illustrating mounting indignation."
        ),
        duration=5.5,
        seed=20261004,
    ),
    StoryClipSpec(
        clip_id='scene05_turn_me_back_now',
        scene_index=5,
        title='"Turn Me Back... NOW!"',
        description='Draco stomps forward screaming at Voldemort, leaving the Dark Lord baffled.',
        prompt=(
            "[Shot 1] At 00:00.000, medium tracking shot: Draco Malfoy stomps furiously toward screen-left, pointing a shaking finger directly at "
            "Lord Voldemort. Draco screams at the top of his lungs with hysterical indignation: \"Turn me back... NOW!\" "
            "[Shot 2] At 00:02.800, cut to medium shot of Lord Voldemort on screen-left, slowly lowering his wand by two inches, "
            "blinking his red slit eyes in rare, utterly confused bewilderment at the absurd scene.\n\n"
            "overall_soundscape: Draco's echoing furious scream, heavy boot stomp on rubble, distant howling wind, awkward silence.\n\n"
            "non_diegetic_music: Bold, exaggerated comedic brass fanfare rising into an awkward, held comedic pause."
        ),
        duration=5.5,
        seed=20261005,
    ),
    StoryClipSpec(
        clip_id='scene06_mcgonagall_intervention',
        scene_index=6,
        title="Professor McGonagall's Holy Counter-Curse",
        description='Professor McGonagall steps up behind them, casting a radiant golden counter-curse.',
        prompt=(
            "[Shot 1] At 00:00.000, medium-wide ensemble shot: From the arches behind Harry and Draco on screen-right, Professor Minerva McGonagall "
            "steps forward with stern, dignified authority, emerald tartan robes swirling. "
            "[Shot 2] At 00:02.300, she executes a crisp, masterful wand flourish. A dazzling beam of warm celestial golden holy light erupts "
            "from her wand tip, creating a shimmering pillar of radiant illumination that completely surrounds Draco Malfoy.\n\n"
            "overall_soundscape: McGonagall's sharp authoritative incantation, warm rushing sound of celestial holy magic, soft chime resonance.\n\n"
            "non_diegetic_music: Grand, ethereal angelic choral swell accompanied by sweeping, warm orchestral strings and shimmering harp."
        ),
        duration=4.8,
        seed=20261006,
    ),
    StoryClipSpec(
        clip_id='scene07_fem_malfoy_returns',
        scene_index=7,
        title='The Holy Restoration to Girl',
        description='Inside the golden light, Draco transfigures back into Female Malfoy with long platinum hair.',
        prompt=(
            "[Shot 1] At 00:00.000, medium shot of the luminous pillar of swirling golden sparkles. Draco's masculine silhouette softens and "
            "gracefully transfigures in a swirl of golden light. "
            "[Shot 2] At 00:02.400, the holy brilliance gently dissolves, revealing Female Malfoy (Lyra Malfoy) fully restored, "
            "with her gorgeous long platinum-blonde hair cascading down her shoulders and elegant feminine Slytherin robes.\n\n"
            "overall_soundscape: Sparkling magical chimes dissolving, soft gentle breeze, delicate fabric rustle.\n\n"
            "non_diegetic_music: Triumphant, magical orchestral resolution with delicate bell chimes and a soaring, majestic French horn harmony."
        ),
        duration=5.0,
        seed=20261007,
    ),
    StoryClipSpec(
        clip_id='scene08_fem_malfoy_relief',
        scene_index=8,
        title='Joyful Relief',
        description='Female Malfoy pats her hair and feminine body, smiling in immense happiness and relief.',
        prompt=(
            "[Shot 1] At 00:00.000, medium shot of Female Malfoy on screen-right excitedly touching her long platinum locks, patting her cheeks, "
            "and smoothing her pleated skirt. Her face glows with immense happiness. "
            "[Shot 2] At 00:02.500, she lets out a huge, breathless sigh of pure relief, her silver eyes shining with sparkling delight "
            "and joyous laughter as Harry and Hermione breathe a sigh of relief beside her.\n\n"
            "overall_soundscape: Soft happy exhale of relief, delighted joyful laugh, gentle rustle of hair.\n\n"
            "non_diegetic_music: Sweet, uplifting lyrical violin melody with cheerful light glockenspiel accents expressing pure joy."
        ),
        duration=5.0,
        seed=20261008,
    ),
    StoryClipSpec(
        clip_id='scene09_how_dare_you',
        scene_index=9,
        title='"How Dare YOU!!!"',
        description='Her face snaps into terrifying, unhinged fury as she glares at Voldemort with righteous wrath.',
        prompt=(
            "[Shot 1] At 00:00.000, medium shot of Female Malfoy. In a split second, her joyful smile completely vanishes, "
            "snapping into an expression of terrifying, unhinged aristocratic fury, her silver eyes flaring with emerald magical fire. "
            "[Shot 2] At 00:01.500, she whirls toward screen-left facing Lord Voldemort, screeching with lethal righteous wrath: \"How dare YOU!!!\"\n\n"
            "overall_soundscape: Sudden violent crash of thunder, deep seismic bass tremor, female shriek echoing with supernatural acoustic force.\n\n"
            "non_diegetic_music: Explosive, monstrous dark orchestral crescendo with slamming taiko drums and menacing, apocalyptic brass."
        ),
        duration=3.2,
        seed=20261009,
    ),
    StoryClipSpec(
        clip_id='scene10_overpowered_finisher',
        scene_index=10,
        title='The Overpowered Super-Spell Finale',
        description='Female Malfoy unleashes a cataclysmic, screen-filling spell that obliterates the view in blinding light.',
        prompt=(
            "[Shot 1] At 00:00.000, medium shot of Female Malfoy thrusting her wand forward with both hands. Concentric shockwaves of emerald and silver lightning "
            "rip up the stone courtyard around her boots. "
            "[Shot 2] At 00:01.400, a gargantuan, incandescent beam of celestial magical power erupts straight towards screen-left and engulfs the camera lens, "
            "filling the entire screen with a blinding, transcendent white-hot magical flash.\n\n"
            "overall_soundscape: Roaring volcanic seismic energy release, tearing stone explosions, blinding cosmic hum, trailing into white noise.\n\n"
            "non_diegetic_music: Earth-shattering full orchestral and choir climax reaching maximum volume, abruptly resolving into a transcendent, high-frequency ringing silence."
        ),
        duration=3.0,
        seed=20261010,
    ),
]
