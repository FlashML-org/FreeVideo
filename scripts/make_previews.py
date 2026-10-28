import subprocess
import os

targets = [
    ('examples_generated/01_pet_cat_sunlight', '01_pet_cat_sunlight_10s.mp4'),
    ('examples_generated/02_human_cyberpunk_detective', '02_human_cyberpunk_detective_10s.mp4'),
    ('examples_generated/03_mythical_phoenix_rebirth', '03_mythical_phoenix_rebirth_10s.mp4'),
    ('examples_generated/04_giant_colossus_forest', '04_giant_colossus_forest_10s.mp4'),
    ('examples_generated/05_alien_bioluminescent_entity', '05_alien_bioluminescent_entity_10s.mp4'),
    ('examples_generated/06_zombie_apocalypse_encounter', '06_zombie_apocalypse_encounter_10s.mp4'),
    ('examples_generated/07_multiscene_harry_and_fem_malfoy', '07_multiscene_harry_and_fem_malfoy_10s.mp4'),
]

for folder, video in targets:
    vpath = os.path.join(folder, video)
    jpg_path = os.path.join(folder, 'poster.jpg')
    gif_path = os.path.join(folder, 'preview.gif')
    if os.path.exists(vpath):
        # 1. extract poster frame
        cmd_jpg = ['ffmpeg', '-y', '-ss', '00:00:01', '-i', vpath, '-vframes', '1', '-q:v', '2', jpg_path]
        subprocess.run(cmd_jpg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 2. extract lightweight preview gif (fps=8, width=280, 2s)
        vf_filter = "fps=8,scale=280:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer"
        cmd_gif = ['ffmpeg', '-y', '-ss', '00:00:01', '-t', '2', '-i', vpath, '-vf', vf_filter, '-loop', '0', gif_path]
        subprocess.run(cmd_gif, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('Processed:', folder, '->', os.path.getsize(jpg_path), 'bytes (jpg),', os.path.getsize(gif_path), 'bytes (gif)')
