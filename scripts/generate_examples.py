"""Batch generation orchestrator for FreeVideo reference examples.

Generates diverse high-quality video demonstrations across categories:
- Pets (playful kitten)
- Humans (cyberpunk detective)
- Mythical creatures (phoenix rebirth)
- Giant creatures (ancient stone colossus)
- Aliens (bioluminescent xenofauna)
- Zombies (post-apocalyptic fog encounter)
- Multi-scene storyboard (Harry Potter & Female Malfoy romance confession)

Outputs are saved in `examples_generated/<example_id>/` along with complete
prompt and generation settings sidecars for community reference.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / 'examples_generated'
COMFY_URL = 'http://127.0.0.1:8189'
COMFY_OUTPUT_ROOT = Path(r'D:\AI\FreeVideoSA\ComfyUI\output\FreeVideo')


@dataclass
class ExampleConfig:
    id: str
    category: str
    title: str
    description: str
    prompt: str
    width: int
    height: int
    seconds: float
    seed: int
    two_pass: bool = True
    base_steps: int = 8
    refine_steps: int = 3
    preview: bool = True


EXAMPLES: List[ExampleConfig] = [
    ExampleConfig(
        id='01_pet_cat_sunlight',
        category='Pets',
        title='Playful Tabby Kitten in Golden Sunbeam',
        description='Close-up macro of an inquisitive kitten batting at floating golden dust motes with soft depth-of-field.',
        prompt=(
            "[Shot 1] A cinematic macro shot at ground level of an adorable, playful ginger tabby kitten in a sunlit "
            "living room. Golden hour sunlight filters through sheer white curtains, illuminating shimmering dust motes "
            "floating in the air. The kitten rolls playfully onto its back on a plush cream-colored rug, batting up "
            "at the light with soft white paws, its amber eyes sparkling with curiosity. The camera glides forward in a "
            "smooth low tracking motion, keeping razor-sharp focus on the soft fur and whisker details with creamy, "
            "cinematic background blur.\n\n"
            "overall_soundscape: Soft rhythmic purring, delicate rustle of carpet fibers, gentle kitten breathing, "
            "and faint melodious birdsong chirping softly outside the window.\n\n"
            "non_diegetic_music: Warm and cheerful acoustic fingerstyle guitar with gentle upright bass and subtle glockenspiel accents."
        ),
        width=448,
        height=320,
        seconds=2.5,
        seed=421001,
    ),
    ExampleConfig(
        id='02_human_cyberpunk_detective',
        category='Humans',
        title='Cyberpunk Detective in Rain-Slicked Neon Alley',
        description='Atmospheric tracking shot of a cyber-augmented investigator in a rainy futuristic metropolis.',
        prompt=(
            "[Shot 1] Cinematic medium close-up of a weary cybernetic detective standing in a rain-slicked alleyway in "
            "a dense cyberpunk city. He wears a dark, wet trench coat with subtle cyan fiber-optic seams. Pours of rain "
            "splash off his shoulders as he exhales a breath of mist into the chilly air. Massive holographic advertisements "
            "in vibrant magenta and electric blue cast sharp neon reflections across dark water puddles. His left eye, a "
            "glowing brass-and-sapphire cybernetic ocular implant, whirs and shifts focus with miniature optical rings. "
            "The camera executes a slow, dramatic dolly track from side to front at eye level.\n\n"
            "overall_soundscape: Heavy rhythmic rain clattering against asphalt, subtle hum of high-voltage neon, "
            "distant drone of flying aerocars, and the tiny mechanical click-whir of the cybernetic lens adjusting.\n\n"
            "non_diegetic_music: Atmospheric analog synthwave drone with slow, pulsating low bass and melancholic synthesizer pads."
        ),
        width=448,
        height=320,
        seconds=2.5,
        seed=832044,
    ),
    ExampleConfig(
        id='03_mythical_phoenix_rebirth',
        category='Mythical Creatures',
        title='The Phoenix Rebirth from Obsidian Embers',
        description='Epic aerial pan of a legendary firebird bursting from embers and ascending into the twilight sky.',
        prompt=(
            "[Shot 1] An epic, cinematic wide shot of an ancient volcanic crater at twilight. Deep within a nest of glowing "
            "obsidian coals and smoldering embers, a majestic mythical Phoenix with incandescent gold, scarlet, and ruby feathers "
            "unfurls its monumental fiery wings. Bursts of golden sparks and trails of liquid starlight swirl upwards as it beats "
            "its wings with immense power. The camera orbits rapidly in a sweeping upward spiral, tracking the firebird as it leaps "
            "skyward, trailing ribbons of radiant aurora flame against dark indigo storm clouds.\n\n"
            "overall_soundscape: Deep volcanic rumble, resonant rhythmic whoosh of colossal fiery wings, crackling embers, "
            "and a piercing, celestial cry echoing across the mountain peaks.\n\n"
            "non_diegetic_music: A soaring orchestral crescendo featuring sweeping strings, triumphant French horns, and ethereal choral harmonies."
        ),
        width=448,
        height=320,
        seconds=2.5,
        seed=991203,
    ),
    ExampleConfig(
        id='04_giant_colossus_forest',
        category='Giant Creatures',
        title='Primordial Stone Colossus in Misty Redwood Valley',
        description='Low-angle hero view of a mountain-sized ancient titan wading through a dense redwood forest.',
        prompt=(
            "[Shot 1] An awe-inspiring extreme low-angle shot from the forest floor looking up through towering ancient "
            "redwood trees. A colossal mountain-sized stone colossus, overgrown with centuries of moss, lichen, and clinging vines, "
            "strides majestically through the foggy valley. Dense morning mist swirls around its stone limbs, and shafts of golden "
            "sunlight pierce through the forest canopy. Each deliberate step shakes the camera subtly and flushes a flock of startled "
            "birds into the misty dawn. The camera tilts slowly upward, accentuating the staggering monumental scale.\n\n"
            "overall_soundscape: Sub-bass seismic earth vibrations, splintering of fallen branches, echoing bird cries, "
            "and a deep resonant groan of ancient grinding stone.\n\n"
            "non_diegetic_music: Epic cinematic percussion, heavy taiko drums, solemn low cellos, and deep brass brass swells."
        ),
        width=448,
        height=320,
        seconds=2.5,
        seed=550312,
    ),
    ExampleConfig(
        id='05_alien_bioluminescent_entity',
        category='Aliens',
        title='Bioluminescent Xenofauna in Crystalline Cavern',
        description='Zero-gravity tracking shot of a glowing extraterrestrial creature gliding past floating crystals.',
        prompt=(
            "[Shot 1] A breathtaking, otherworldly sci-fi shot inside a vast subterranean alien cavern filled with giant, "
            "levitating purple and cyan amethyst crystals. A majestic, translucent extraterrestrial being with undulating ribbon-like "
            "fins and glowing bioluminescent tendrils glides weightlessly through the zero-gravity air. Pulsating waves of teal, "
            "violet, and aquamarine light wash across its pearlescent skin, casting iridescent reflections on the surrounding crystal facets. "
            "The camera glides effortlessly on a 3D arc, weaving smoothly between suspended crystals.\n\n"
            "overall_soundscape: Subaquatic crystalline chimes, harmonic alien reverberations, subtle air shimmers, "
            "and deep resonant pulses corresponding to the creature's bioluminescent flashes.\n\n"
            "non_diegetic_music: Ethereal ambient space music with shimmering modular synthesizer bells and warm deep sub-bass pads."
        ),
        width=448,
        height=320,
        seconds=2.5,
        seed=714902,
    ),
    ExampleConfig(
        id='06_zombie_apocalypse_encounter',
        category='Zombies',
        title='Post-Apocalyptic Zombie in Urban Fog',
        description='Atmospheric tracking push-in through ruined street fog as a flashlight illuminates a walking shadow.',
        prompt=(
            "[Shot 1] A cinematic, tense horror sequence set on a crumbling post-apocalyptic city highway overpass shrouded in "
            "thick cold fog. Overturned rusted vehicles and scattered debris line the cracked asphalt. In the distance, a solitary "
            "zombie figure with tattered dark clothes and hollow, vacant eyes limps forward through the mist. A harsh conical beam "
            "from a survivor's trembling flashlight cuts through the gloom, casting elongated eerie shadows against concrete barriers. "
            "The camera moves in a slow, suspenseful forward tracking shot, low to the ground, maintaining steady forward momentum.\n\n"
            "overall_soundscape: Distant mournful wind howling through deserted skyscrapers, scraping of dragging boots on wet pavement, "
            "ragged raspy breath, and the intermittent click of an electric flashlight.\n\n"
            "non_diegetic_music: Dark cinematic tension strings with dissonant cello scrapes and deep, heartbeat-like sub-bass thumps."
        ),
        width=448,
        height=320,
        seconds=2.5,
        seed=638201,
    ),
    ExampleConfig(
        id='07_multiscene_harry_and_fem_malfoy',
        category='Multi-Scene Storyboard',
        title='Forbidden Moonlight Confession: Harry Potter and Female Malfoy',
        description='3-shot emotional storyboard in Hogwarts Astronomy Tower: tense confrontation, tearful confession, and fainting into arms.',
        prompt=(
            "[Shot 1] Midnight in the Gothic Astronomy Tower of Hogwarts during a heavy thunderstorm. Cold moonlight and flashes "
            "of lightning spill through tall lancet arched windows onto wet flagstone floors. Female Malfoy (Lyra Malfoy) — aristocratic, "
            "with long sleek platinum-blonde hair tumbling over emerald green and silver Slytherin robes, stormy silver eyes filled with "
            "pain — stands trembling near the carved stone railing. A few paces away, Harry Potter stands in a dark Gryffindor wool coat, "
            "his messy black hair and round wire-rimmed glasses illuminated by a flash of lightning. The camera tracks slowly past Gothic "
            "arches in a dramatic cinematic wide two-shot.\n"
            "[Shot 2] At 00:03.000, the camera glides into an emotional medium close-up on Female Malfoy as she takes an unsteady step forward. "
            "Her breath shivers in the cold air, and tears brim in her silver eyes as her aristocratic facade completely shatters. "
            "Her lips tremble as she whispers desperately: \"I've spent years hating everything about you, Potter... but God forgive me, "
            "I hate how much I love you even more.\" Outside, rain lashes violently against the stained glass windows.\n"
            "[Shot 3] At 00:07.000, completely overcome by exhaustion, sorrow, and raw emotion, her silver eyes flutter shut and her knees buckle. "
            "Harry instantly lunges forward and catches her securely in his arms before she touches the cold stone floor, pulling her frail frame "
            "tenderly against his chest. The camera completes a poignant, slow 360-degree orbit around them as moonlight softens through the "
            "clearing storm clouds, framing their forbidden embrace.\n\n"
            "overall_soundscape: Heavy rain hammering the stone roof, howling mountain wind through arched balconies, rolling distant thunder, "
            "soft ragged gasps, and the gentle rustle of heavy wool robes.\n\n"
            "non_diegetic_music: A deeply moving, melancholic solo piano piece with delicate high melodies and sorrowful lower chords, "
            "swelling tenderly during the confession, then resolving into a soft, resonant chord as she falls into his arms."
        ),
        width=512,
        height=288,
        seconds=10.0,
        seed=19980502,
    ),
]


def check_server() -> bool:
    """Verify ComfyUI server is listening on port 8189."""
    try:
        with urllib.request.urlopen(f'{COMFY_URL}/system_stats', timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_for_queue_idle(timeout_seconds: int = 600) -> bool:
    """Wait until existing active queue jobs finish."""
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            with urllib.request.urlopen(f'{COMFY_URL}/queue', timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                running = data.get('queue_running', [])
                if not running:
                    return True
                print(f'Waiting for current job ({len(running)} active)...', end='\r', flush=True)
        except Exception:
            pass
        time.sleep(3)
    return False


def submit_prompt(config: ExampleConfig) -> str:
    """Submit a single FreeVideoGenerate node workflow to ComfyUI."""
    w = int(max(256, (config.width // 32) * 32))
    h = int(max(256, (config.height // 32) * 32))
    payload = {
        'prompt': {
            '1': {
                'class_type': 'FreeVideoGenerate',
                'inputs': {
                    'text': config.prompt,
                    'width': w,
                    'height': h,
                    'seconds': config.seconds,
                    'seed': config.seed,
                    'two_pass': config.two_pass,
                    'base_steps': config.base_steps,
                    'refine_steps': config.refine_steps,
                    'preview': config.preview,
                    'force_regenerate': True,
                }
            }
        }
    }
    req = urllib.request.Request(
        f'{COMFY_URL}/prompt',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode('utf-8'))
        return res['prompt_id']


def find_latest_video_after(timestamp_marker: float) -> Optional[Path]:
    """Search for the most recently created video.mp4 under ComfyUI output directory."""
    if not COMFY_OUTPUT_ROOT.is_dir():
        return None
    matches = []
    for mp4 in COMFY_OUTPUT_ROOT.rglob('video.mp4'):
        try:
            mtime = mp4.stat().st_mtime
            if mtime >= timestamp_marker - 2.0:
                matches.append((mtime, mp4))
        except OSError:
            continue
    if matches:
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[0][1]
    return None


def write_reference_txt(dest_dir: Path, config: ExampleConfig, video_file: Path) -> Path:
    """Write sidecar .txt file containing prompt, camera notes, and settings."""
    txt_path = dest_dir / f'{config.id}_prompt_and_settings.txt'
    size_mb = video_file.stat().st_size / (1024 * 1024)
    content = f"""======================================================================
FreeVideo & MiniMax H3 Community Showcase Reference
======================================================================
Title:       {config.title}
Category:    {config.category}
Identifier:  {config.id}
File:        {video_file.name} ({size_mb:.2f} MB)
Timestamp:   {time.strftime('%Y-%m-%d %H:%M:%S')}

----------------------------------------------------------------------
[1] Narrative & Style Description
----------------------------------------------------------------------
{config.description}

----------------------------------------------------------------------
[2] Multimodal Prompt (Visual, Soundscape & Music)
----------------------------------------------------------------------
{config.prompt}

----------------------------------------------------------------------
[3] Generation Parameters & Settings
----------------------------------------------------------------------
- Architecture:        FreeVideo Pascal / MiniMax H3 (T2VA)
- Resolution:          {config.width} x {config.height}
- Aspect Ratio:        {config.width}:{config.height}
- Duration (seconds):  {config.seconds}s
- Random Seed:         {config.seed}
- Two-Pass Mode:       {config.two_pass}
- First-Pass Steps:    {config.base_steps}
- Second-Pass Steps:   {config.refine_steps}
- Preview Mode:        {config.preview}
======================================================================
"""
    txt_path.write_text(content, encoding='utf-8')
    return txt_path


def update_catalog_readme() -> None:
    """Generate or update examples_generated/README.md catalog."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    readme_path = OUTPUT_DIR / 'README.md'
    lines = [
        '# FreeVideo Showcase & Compatibility Examples',
        '',
        'This folder contains community reference videos and prompts demonstrating the capabilities',
        'of the FreeVideo standalone engine and MiniMax H3 multimodal generation pipeline.',
        '',
        '| Category | Title | Resolution | Duration | Files |',
        '| :--- | :--- | :---: | :---: | :--- |',
    ]
    for ex in EXAMPLES:
        folder = OUTPUT_DIR / ex.id
        video = folder / f'{ex.id}.mp4'
        txt = folder / f'{ex.id}_prompt_and_settings.txt'
        status = '✓ Generated' if video.is_file() else '⏳ Pending'
        lines.append(f'| **{ex.category}** | {ex.title} | {ex.width}x{ex.height} | {ex.seconds}s | [{status}]({ex.id}/) |')

    lines.extend([
        '',
        '## Highlights & Included Examples',
        '1. **Pets**: Macro close-up of a kitten playing in sunlight with realistic fur and purring.',
        '2. **Humans**: Cyberpunk detective in a rainy neon-lit alleyway with tracking camera motion.',
        '3. **Mythical Creatures**: Ancient phoenix rising from embers with volumetric flame trails.',
        '4. **Giant Creatures**: Colossal stone titan towering over redwood trees with low-angle perspective.',
        '5. **Aliens**: Weightless bioluminescent creature swimming past levitating amethyst crystals.',
        '6. **Zombies**: Post-apocalyptic fog sequence with dynamic flashlight shadows.',
        '7. **Multi-Scene Storyboard**: Harry Potter & Female Malfoy 3-shot dramatic romance confession in the Hogwarts Astronomy Tower.',
    ])
    readme_path.write_text('\n'.join(lines), encoding='utf-8')


def run_batch() -> None:
    """Run full overnight generation batch."""
    print('===================================================')
    print('[FreeVideo] Overnight Example Generator')
    print('===================================================')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not check_server():
        print(f'[ERROR] Could not connect to ComfyUI at {COMFY_URL}.')
        print('Please make sure FreeVideo / ComfyUI is running on port 8189.')
        sys.exit(1)

    print('[INFO] Connected to FreeVideo ComfyUI service.')
    update_catalog_readme()

    for idx, config in enumerate(EXAMPLES, start=1):
        target_folder = OUTPUT_DIR / config.id
        target_folder.mkdir(parents=True, exist_ok=True)
        final_video = target_folder / f'{config.id}.mp4'

        print(f'\n[{idx}/{len(EXAMPLES)}] Processing: {config.title} ({config.category})')
        if final_video.is_file() and final_video.stat().st_size > 10000:
            print(f'  [SKIP] Video already exists: {final_video}')
            write_reference_txt(target_folder, config, final_video)
            continue

        print('  Waiting for queue readiness...')
        wait_for_queue_idle()

        marker_time = time.time()
        print(f'  Submitting generation prompt (Seed: {config.seed}, Resolution: {config.width}x{config.height})...')
        prompt_id = submit_prompt(config)
        print(f'  Prompt queued with ID: {prompt_id}')

        # Monitor execution
        start_time = time.time()
        completed = False
        while time.time() - start_time < 3600:  # 1 hour max per video
            time.sleep(10)
            elapsed = int(time.time() - start_time)
            print(f'  Generating... ({elapsed}s elapsed)', end='\r', flush=True)

            try:
                with urllib.request.urlopen(f'{COMFY_URL}/history/{prompt_id}', timeout=5) as h_resp:
                    history = json.loads(h_resp.read().decode('utf-8'))
                    if prompt_id in history:
                        completed = True
                        break
            except Exception:
                pass

        print()
        if not completed:
            print(f'  [WARNING] Generation timed out or failed for {config.id}.')
            continue

        # Locate produced video
        time.sleep(3)
        found_video = find_latest_video_after(marker_time)
        if found_video and found_video.is_file():
            shutil.copyfile(found_video, final_video)
            print(f'  [SUCCESS] Saved video: {final_video}')
            txt_path = write_reference_txt(target_folder, config, final_video)
            print(f'  [SUCCESS] Saved prompt & settings: {txt_path}')
            update_catalog_readme()
        else:
            print(f'  [ERROR] Completed prompt {prompt_id} but output video file was not found.')

    print('\n===================================================')
    print('[FreeVideo] All batch examples completed!')
    print(f'Results stored in: {OUTPUT_DIR}')
    print('===================================================')


if __name__ == '__main__':
    run_batch()
