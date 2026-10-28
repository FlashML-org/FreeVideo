"""Multi-Tier Showcase Specifications and Pop-Culture Multimodal Prompts.

Defines the reference configurations, prompts, and continuation descriptors
for FreeVideo & MiniMax H3 across duration tiers and character recognition tests:
- 01: Goku Meets Mr. Bean (Comedic)
- 02: Neo vs. John Wick (Matrix Martial Arts / Gun-Fu)
- 03: Godzilla vs. Dragonzord (MMPR Kaiju Battle)
- 04: Starfleet Away Team vs. Xenomorph (Sci-Fi Horror)
- 05: Zombie Horde vs. Combat Droids & Drone Swarm (Futuristic Warfare)
- 06: Harry Potter & Female Malfoy 5-Clip Montage Suite (Movements I to V)
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class MultiTierSpec:
    id: str
    category: str
    title: str
    base_description: str
    base_prompt: str
    continuation_prompt: str
    base_seed: int
    width: int
    height: int


@dataclass
class MontageClipSpec:
    clip_id: str
    movement: str
    title: str
    description: str
    prompt: str
    reference_image_file: str
    seed: int
    duration: float = 5.0
    width: int = 512
    height: int = 288


# 5 Pop-Culture Scenarios (Tiers 5s, 10s, 15s)
SPECS: List[MultiTierSpec] = [
    MultiTierSpec(
        id='01_goku_meets_mr_bean',
        category='Comedic Pop-Culture',
        title='Goku Meets Mr. Bean: The Ramen Bowl Challenge',
        base_description='Son Goku in orange gi slurps an impossible tower of ramen while Mr. Bean in tweed blazer reacts in bug-eyed astonishment.',
        base_prompt=(
            "[Shot 1] A cinematic comedic shot at eye level in a sunlit outdoor ramen diner on a bustling city street. "
            "Son Goku, wearing his bright orange martial arts gi with navy blue undershirt and wristbands, his spiked black anime hair "
            "striking against the sky, sits on a wooden bench eagerly devouring a two-meter tower of steaming ramen bowls with superhuman speed, "
            "chopsticks moving in a blur. Sitting opposite him is Mr. Bean, dressed in his classic brown tweed jacket, skinny red tie, and white collar. "
            "Mr. Bean's eyes bulge in comic disbelief, his rubbery eyebrows twitching with dramatic agitation. He awkwardly picks up a single chopstick, "
            "attempts to slurp an oversized noodle with flamboyant concentration, and accidentally flips his bowl into his lap with a flustered expression.\n\n"
            "overall_soundscape: Superhuman noodle slurping, clattering porcelain bowls, bubbling broth, Mr. Bean's signature muffled vocal grunts and squeaks, "
            "and distant lively city chatter.\n\n"
            "non_diegetic_music: Playful, jaunty orchestral slapstick comedy score featuring bouncy pizzicato strings, comedic bassoon melodies, and lively xylophone accents."
        ),
        continuation_prompt=(
            "[Shot 1] Mr. Bean frantically wipes his tweed jacket with a tiny napkin, giving an exaggerated sheepish grin to the camera, while Goku cheerfully "
            "slams down his final empty ceramic bowl, lets out a hearty contented laugh, pats his stomach, and offers Mr. Bean a cheerful thumbs up.\n\n"
            "overall_soundscape: Frantic napkin rubbing, ceramic bowl clanking on wood, Goku's hearty joyful laugh, gentle street ambiance.\n\n"
            "non_diegetic_music: The jaunty comedy theme resolves into a triumphant, cheerful brass flourish and final pizzicato pluck."
        ),
        base_seed=9021001,
        width=448,
        height=320,
    ),
    MultiTierSpec(
        id='02_neo_vs_john_wick',
        category='Action & Martial Arts',
        title='Neo vs. John Wick: Rain-Slicked Rooftop Duel',
        base_description='Neo in flowing black Matrix trench coat confronts John Wick in tailored bulletproof suit on a rainy neon rooftop.',
        base_prompt=(
            "[Shot 1] A cinematic, rain-drenched nighttime rooftop showdown atop a neo-noir skyscraper. On the left stands Neo, clad in his "
            "flowing black Matrix trench coat and sleek oval sunglasses, rainwater glistening across his jaw as he raises his right hand into "
            "his iconic two-finger beckoning kung-fu stance. Five paces away stands John Wick, dressed in his tailored charcoal bulletproof "
            "three-piece suit and dark tie, his damp hair framing an intensely focused expression. Wick fluidly racks the tactical slide of his "
            "custom pistol, holsters it with practiced efficiency, and lunges into close-quarters combat. Neo smoothly ducks an explosive cross in "
            "slow-motion bullet time, ripples distorting the falling rain as he counters with a spinning palm strike.\n\n"
            "overall_soundscape: Torrential rain hammering rooftop gravel, whistling air friction from supersonic martial arts strikes, "
            "leather trench coat swishing violently, tactical slide racking, and distant rolling thunder.\n\n"
            "non_diegetic_music: Driving industrial breakbeat techno infused with deep distorted synth bass pulses and aggressive hybrid cello ostinatos (Don Davis and Tyler Bates style)."
        ),
        continuation_prompt=(
            "[Shot 1] John Wick absorbs the impact, spinning into a low hip throw, but Neo defies gravity, hovering momentarily above the wet gravel before "
            "landing gracefully. Both legendary fighters reset their stances simultaneously, rain splashing around their boots, locking eyes with mutual respect.\n\n"
            "overall_soundscape: Heavy splashing boots, breathing in the cold rain, distant city siren echoing between skyscrapers.\n\n"
            "non_diegetic_music: The industrial breakbeat builds with thunderous sub-bass drops and soaring electric cello chords into a dramatic crescendo."
        ),
        base_seed=8032044,
        width=448,
        height=320,
    ),
    MultiTierSpec(
        id='03_godzilla_vs_dragonzord',
        category='Kaiju & Mecha',
        title='Godzilla vs. Dragonzord: Clash of the Titans',
        base_description='Godzilla with glowing atomic blue spines clashes with the Green Ranger Dragonzord in a neon Tokyo Bay.',
        base_prompt=(
            "[Shot 1] An epic cinematic twilight battle in the churning waters of Tokyo Bay. Godzilla, the colossal King of the Monsters with dark "
            "rugged obsidian scales and glowing neon-blue dorsal spines, wades aggressively toward the shoreline, unleashing his deafening, iconic resonant "
            "roar that shatters glass windows across nearby illuminated skyscrapers. Directly opposing him, the mechanical titan Dragonzord surfaces "
            "from the harbor, its emerald-and-gold chest emblem gleaming under storm lightning, black armored plates whirring with hydraulic power. "
            "Dragonzord's massive drill-tipped tail spins at high velocity as it takes a heavyweight combat stance, bracing against Godzilla as lightning tears the stormy indigo sky.\n\n"
            "overall_soundscape: Godzilla's earth-shaking primal roar, mechanical hydraulic groans, high-speed drill whir, tidal waves crashing against seawalls, and thunderous lightning.\n\n"
            "non_diegetic_music: Monumental brass-heavy orchestral kaiju march punctuated with driving heavy metal electric guitar riffs inspired by the classic Mighty Morphin Power Rangers battle anthem."
        ),
        continuation_prompt=(
            "[Shot 1] Godzilla lunges forward with an immense claws-forward swipe, while Dragonzord spins its whirling drill-tail in a defensive arc, "
            "sparks cascading violently across the bay as the two colossal titans grapple amidst crumbling harbor docks and rising steam.\n\n"
            "overall_soundscape: Explosive metal-on-hide impact, shower of electrical sparks, deep resonant mechanical groan, Godzilla's guttural battle snarl.\n\n"
            "non_diegetic_music: Heavy double-kick drums and roaring brass fanfare reach an earth-shaking peak with shredding electric guitar harmonies."
        ),
        base_seed=7055031,
        width=448,
        height=320,
    ),
    MultiTierSpec(
        id='04_startrek_vs_xenomorph',
        category='Sci-Fi Horror',
        title='Starfleet Away Team vs. The Xenomorph',
        base_description='Starfleet officers with phasers and medical tricorder investigate a derelict starship corridor as a Xenomorph stalk them.',
        base_prompt=(
            "[Shot 1] A tense, dark sci-fi horror sequence inside the dripping bio-mechanical corridors of a derelict starship. Two Starfleet officers "
            "in gold operations and red command duty uniforms advance cautiously, holding glowing Type-2 phaser pistols. The lead officer scans the "
            "shadowy corridor with a medical tricorder, its rhythmic blue diagnostic chime accelerating into a frantic high-pitch beep. Suddenly, in "
            "the overhead ceiling conduits above them, an obsidian Xenomorph uncoils its biomechanical ribbed tail. Cold steam hisses from ruptured pipes "
            "as the alien's elongated glossy skull slides into the flickering amber emergency lights, baring its dripping metallic secondary jaw in a silent, predatory snarl.\n\n"
            "overall_soundscape: Rapid high-pitch tricorder chirps, hissing high-pressure steam pipes, wet guttural alien hissing, hum of energized phaser capacitors, and dripping condensation.\n\n"
            "non_diegetic_music: Claustrophobic orchestral suspense score featuring dissonant French horns, rising microtonal string clusters, and militaristic snare taps (Jerry Goldsmith and James Horner aesthetic)."
        ),
        continuation_prompt=(
            "[Shot 1] The lead officer spins upward, firing a brilliant beam of orange phaser energy that illuminates the entire corridor. The Xenomorph "
            "screeches, leaping across ceiling bulkheads with terrifying agility as shadows distort along the steel walls.\n\n"
            "overall_soundscape: Piercing phaser discharge beam, Xenomorph metallic screech, clawed feet scrambling on corrugated steel, echo of running boots.\n\n"
            "non_diegetic_music: Chaotic, pounding orchestral percussion with screeching brass and frantic violins building into a pulse-pounding climax."
        ),
        base_seed=6071490,
        width=448,
        height=320,
    ),
    MultiTierSpec(
        id='05_zombies_vs_droids',
        category='Futuristic Warfare',
        title='Zombie Horde vs. Combat Droids and Drone Swarm',
        base_description='Swarm of post-apocalyptic infected assault a fortified checkpoint defended by tactical combat droids and attack drones.',
        base_prompt=(
            "[Shot 1] An apocalyptic battlefield on a ruined highway overpass at midnight. An overwhelming horde of ravenous, rotting zombies surges "
            "through thick cold fog, scaling overturned rusted vehicles and concrete barriers. Holding the defensive line is a squad of sleek bipedal "
            "combat droids with matte-grey armor plating and glowing cyan optical visors, firing synchronized plasma bursts from tactical rifles with "
            "unflinching robotic precision. Overhead, a swarm of autonomous quadrotor attack drones buzzes dynamically through the smoke, illuminating "
            "the rushing horde with green targeting lasers and unleashing devastating micro-missile barrages into the mob.\n\n"
            "overall_soundscape: Howling guttural zombie shrieks, rapid automated pulse-rifle discharges, high-frequency whine of drone rotors, "
            "exploding micro-missile detonations, and crunching debris.\n\n"
            "non_diegetic_music: Aggressive darksynth cyberpunk score with heavy distorted synthwave basslines, pounding electro-industrial drums, and dark choir swells."
        ),
        continuation_prompt=(
            "[Shot 1] The combat droids step forward in unified lockstep, their visor optics shifting from cyan to combat crimson as the drone swarm "
            "forms a sweeping perimeter strike, clearing the overpass in a dazzling cascade of plasma fire and illuminated smoke.\n\n"
            "overall_soundscape: Heavy metallic stomping footsteps, automated mechanical reload clicks, burning embers crackling, dying shrieks in the fog.\n\n"
            "non_diegetic_music: Pounding four-on-the-floor synthwave bass with soaring arpeggiated synthesizers and triumphant distorted lead chords."
        ),
        base_seed=5063820,
        width=448,
        height=320,
    ),
]


# 5-Clip Montage Suite: Harry Potter & Female Malfoy (Lyra Malfoy)
# Unified Musical Theme: "The Astronomy Tower Suite in D Minor" (Solo Piano & Solo Cello, 68 BPM)
MONTAGE_CLIPS: List[MontageClipSpec] = [
    MontageClipSpec(
        clip_id='clip1_hogwarts_express',
        movement='Movement I: The Corridor Allegro',
        title='Year 1: The Hogwarts Express First Meeting',
        description='Young 11-year-old Harry meets young Female Malfoy (Lyra Malfoy) at the compartment door.',
        prompt=(
            "[Shot 1] At 00:00.000, inside a wooden compartment of the Hogwarts Express train under golden afternoon sunlight. "
            "Young 11-year-old Harry Potter with messy black hair, round wireframe glasses, and an oversized grey jumper looks up. "
            "Standing in the open compartment doorway is young Female Malfoy (Lyra Malfoy), aristocratic with sleek platinum-blonde hair, "
            "a silver serpent hairpin, and crisp black robes with a green Slytherin tie. She holds her chin high with a haughty, confident smirk, "
            "extending a gloved hand toward Harry as countryside hills roll past the train window.\n\n"
            "overall_soundscape: Rhythmic clatter of steam train tracks, gentle whistle in the distance, soft rustle of school robes.\n\n"
            "non_diegetic_music: The Astronomy Tower Suite in D Minor - Movement I (Corridor Allegro): Inquisitive, elegant solo concert grand piano "
            "arpeggios at 68 BPM establishing the melancholic main theme with delicate high melodic ornamentations."
        ),
        reference_image_file='malfoid-anime.png',
        seed=19980101,
        duration=5.0,
        width=512,
        height=288,
    ),
    MontageClipSpec(
        clip_id='clip2_quidditch_rivalry',
        movement='Movement II: The High Wind Scherzo',
        title='Year 3: Stormy Quidditch Pitch Snitch Chase',
        description='Harry and Female Malfoy dive on broomsticks inches apart chasing the Golden Snitch.',
        prompt=(
            "[Shot 1] At 00:00.000, a dramatic Quidditch match above the Hogwarts pitch during a torrential rainstorm. Harry Potter in scarlet "
            "Gryffindor robes and Female Malfoy in emerald Slytherin robes dive at breakneck speed on racing broomsticks side by side. Rain lashes their "
            "faces and her long platinum-blonde hair whips wildly in the wind. Just inches ahead, the glittering Golden Snitch darts erratically. "
            "She cuts her broom inward, glaring at Harry with fierce competitive fire before flashing a defiant, thrilling smirk.\n\n"
            "overall_soundscape: Howling gale wind, rain lashing robes, fluttering golden snitch wings, thunder rolling across the stadium.\n\n"
            "non_diegetic_music: The Astronomy Tower Suite in D Minor - Movement II (High Wind Scherzo): Rapid, driving piano arpeggios continuing "
            "seamlessly at 68 BPM, punctuated by urgent staccato low cello accents matching the broomstick chase."
        ),
        reference_image_file='malfoid-animation.png',
        seed=19980202,
        duration=5.0,
        width=512,
        height=288,
    ),
    MontageClipSpec(
        clip_id='clip3_yule_ball_longing',
        movement='Movement III: The Ballroom Adagio',
        title='Year 4: The Yule Ball Unspoken Longing',
        description='Female Malfoy in an emerald silk evening gown locks eyes with Harry in the Great Hall.',
        prompt=(
            "[Shot 1] At 00:00.000, the enchanting Hogwarts Great Hall transformed for the Yule Ball with glittering ice sculptures and floating "
            "silver candles. Female Malfoy, dressed in a breathtaking floor-length emerald-silk backless evening gown, her platinum-blonde hair "
            "arranged in soft, delicate curls, pauses beside a frosted stone pillar. Across the bustling ballroom crowd, she locks eyes with Harry "
            "Potter in his formal dress robes. Her aristocratic, haughty mask softens for a fleeting moment into an expression of quiet longing and raw vulnerability.\n\n"
            "overall_soundscape: Soft rustling of silk gowns, gentle clinking of crystal goblets, muffled romantic ballroom murmurs, distant ice fountain chime.\n\n"
            "non_diegetic_music: The Astronomy Tower Suite in D Minor - Movement III (Ballroom Adagio): The main piano theme transforms into a deeply emotional, "
            "poignant waltz variation at 68 BPM, with warm expressive solo cello melodies swelling in rich harmonic counterpoint."
        ),
        reference_image_file='malfoid-realistic.png',
        seed=19980303,
        duration=5.0,
        width=512,
        height=288,
    ),
    MontageClipSpec(
        clip_id='clip4_vanishing_cabinet',
        movement='Movement IV: The Dark Tower Crescendo',
        title='Year 6: The Vanishing Cabinet Crisis',
        description='Female Malfoy grips her wand in tears in the dark Astronomy Tower as Harry pleads with her.',
        prompt=(
            "[Shot 1] At 00:00.000, midnight in the shadow-draped Astronomy Tower during a fierce thunderstorm. Lightning flashes through Gothic lancet "
            "windows, illuminating Female Malfoy in disheveled Slytherin robes trembling near the stone balustrade. Tears spill from her stormy silver eyes "
            "as she grips her wand with pale, shaking fingers. Harry stands a few paces away with hands raised in desperate entreaty, pleading with her: "
            "\"You don't have to do this, Lyra!\" She looks at him with utter heartbreak, her lip quivering in agony.\n\n"
            "overall_soundscape: Heavy rain hammering the stone roof, rolling boom of thunder, ragged emotional gasps, cold wind howling through arches.\n\n"
            "non_diegetic_music: The Astronomy Tower Suite in D Minor - Movement IV (Dark Tower Crescendo): Dissonant, deeply sorrowful minor piano chords "
            "at 68 BPM rising into an agonizing emotional crescendo, underscored by weeping solo cello harmonics."
        ),
        reference_image_file='malfoid-mix.png',
        seed=19980404,
        duration=5.0,
        width=512,
        height=288,
    ),
    MontageClipSpec(
        clip_id='clip5_dawn_reconciliation',
        movement='Movement V: The Dawn Resolution',
        title='Aftermath: Dawn Embrace in the Ruined Courtyard',
        description='Harry and Female Malfoy sit together on a fallen pillar wrapped in his coat as sunrise breaks.',
        prompt=(
            "[Shot 1] At 00:00.000, dawn breaking over the ruined Hogwarts stone courtyard after the Battle. Golden morning sunlight pierces the clearing "
            "smoke and mist. Harry Potter and Female Malfoy sit close together on a shattered stone pillar, wrapped together inside Harry's dark Gryffindor "
            "wool coat. Her head rests peacefully on his shoulder, her platinum hair catching the golden light, their fingers gently intertwined. "
            "The camera executes a slow, poignant 360-degree orbit around them, capturing their shared peace amidst the calm aftermath.\n\n"
            "overall_soundscape: Soft morning breeze rustling through ivy, distant melodic birdsong chirping, quiet shared breathing, gentle wool fabric rustle.\n\n"
            "non_diegetic_music: The Astronomy Tower Suite in D Minor - Movement V (Dawn Resolution): The sorrowful minor theme at 68 BPM resolves "
            "tenderly into warm, radiant D Major with resonant grand piano arpeggios and a soothing, deeply touching final solo cello resolution."
        ),
        reference_image_file='malfoid-realistic.png',
        seed=19980505,
        duration=5.0,
        width=512,
        height=288,
    ),
]
