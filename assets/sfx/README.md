# Sound effects (earshot)

Rendered once from the open-source **earshot** sound bank (github.com/Leonxlnx/earshot, MIT, sounds made in code —
no recordings, no licence limits). `pipeline/render.py:make_real_bed` places them on the reel timeline.

| file | earshot id | used at |
|---|---|---|
| whoosh.wav | whoosh_fast/crisp | each tip card + save card (peak lands on the cut) |
| mistake.wav | fit/hit/bamboo_tok | the "mistake" line |
| fix.wav | pro_notify/ding_1930 | the "fix" line |
| save.wav | pro_click/soft | "save this" |
| riser.wav | riser/snatch | into the end card |
| cta.wav | pro_impact/short | end card hit |
| whoosh2.wav, pop.wav | fit/whoosh.short/bamboo_swish, pop/0 | spare |

Re-render / swap a sound: `pip install git+https://github.com/Leonxlnx/earshot` then
`earshot sfx list` and `earshot sfx render <id> -o assets/sfx/<name>.wav`.
