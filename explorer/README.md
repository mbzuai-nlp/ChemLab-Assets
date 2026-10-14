---
title: ChemLab-Assets Explorer
emoji: 🧪
colorFrom: blue
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: Browse 198 simulation-ready chemistry lab instruments in 3D
datasets:
  - MBZUAI/ChemLab-Assets
tags:
  - chemistry
  - laboratory
  - robotics
  - embodied-ai
  - simulation
  - 3d
  - urdf
  - three.js
---

# ChemLab-Assets Explorer

An interactive 3D viewer for [ChemLab-Assets](https://huggingface.co/datasets/MBZUAI/ChemLab-Assets), an open dataset of 198 simulation-ready chemistry laboratory instruments in 16 domains. Pick an instrument to see its model with working joints (sliders move doors, lids, knobs and stages), collision proxies, interface points, panel readouts and placement frames, and click any part to identify it. The side panel lists the instrument's components, joints, capabilities, state slots and physical mechanisms, shows its preview renders, and links to the GLB, URDF, asset contract and Python runtime. A sortable table compares all 198 instruments. Part and mechanism descriptions are kept in their original Chinese.

Link straight to an instrument with `#instrument.<slug>`, for example [`#instrument.esco_efa5uduvw`](https://huggingface.co/spaces/MBZUAI/ChemLab-Assets-Explorer#instrument.esco_efa5uduvw) for the fume hood; `?id=instrument.<slug>` works as well. The address follows your selection, so you can copy it to share a view.

This Space holds only the viewer, a catalogue index and one summary file per instrument (`data/`), and thumbnails (`media/`). Models, preview renders and downloads are read directly from the dataset repository. To try it locally, serve this folder with any static web server; `?base=<url>` points the viewer at another copy of the dataset (that server must send `Access-Control-Allow-Origin`).

**Licence.** The viewer code is MIT-licensed, and so is the bundled [three.js](https://threejs.org) r160 (`vendor/LICENSE`). The instrument assets are licensed CC BY-NC 4.0 by the [dataset](https://huggingface.co/datasets/MBZUAI/ChemLab-Assets); see its card for terms and citation.
