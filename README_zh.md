<div align="center">

<img src="docs/media/banner.jpg" alt="198 台仪器中的 24 台，统一影棚渲染" width="100%">

# ChemLab-Assets

**198 台可直接用于仿真的化学实验室仪器资产**<br>
零件级几何 · 可动关节 · 可执行的行为模型 · 机器可读的资产合同

Zirui Song · Jiadong Zhang · Jiahao Zhang · Xiaosong Ma · Xiaojun Chang · Xiuying Chen<br>
Mohamed bin Zayed University of Artificial Intelligence (MBZUAI)

[English](README.md) · **中文**

</div>

## 更新动态

* **2026-10-15 · 1.1 版**：输出模板与反应数据。
  * 第二批基础仪器 36 台全部配上真实数据模板。其中 18 个模板库接入了各自的运行层，涉及气相色谱、液相色谱、顶空进样、红外、紫外可见、台式核磁、粉末 XRD、电化学工作站、卡尔费休、pH/电导、自动滴定、旋转蒸发、冻干、固相萃取、微波消解、离心机和质量流量计。另有 5 台反应类仪器由共享反应模板覆盖，13 台没有与物质相关的读数，标为不适用。
  * 共享反应模板从 8 条扩到 38 条，反应物种从 21 种扩到 73 种，每条都能由 Matterix 反应过程执行，并已在 Isaac Lab 中检查。
  * 公共化合物表从 95 种扩到 200 种（GC 保留、红外、EI 质谱、紫外、¹H/¹³C 核磁、HPLC），并修正了 JCAMP-DX 红外解码。新增"化合物 ↔ 仪器"对照表（`assets/reactions/shared_id_crosswalk.json`、`scripts/template_library.py`），可以查到某个化合物在哪些仪器上有真实模板。
  * 补强了 32 个偏弱的模板库，大多补到运行层可用的真实记录不少于 50 条。例如 TGA 从 2 种增至 65 种材料，DMA 从 1 种增至 59 种，纳米压痕从 2 种增至 55 种，CPL 从 1 个增至 57 个发光体。6 台元素分析仪现在共用一个标准物质证书库，含 121 种标准物质、2836 条证书值。
  * 所有改动过的仪器都重新跑了单元测试、合同校验和 Isaac Lab 场景验证。
* **2026-10-14 · 1.0 版**：首次公开发布，包括 198 台仪器及其合同、运行层、测试，以及 3D 浏览器和项目网站。

## 简介

在化学实验室里工作的机器人和智能体，光有网格模型是不够的。它们需要知道：通风柜移门能拉开 673 mm、在 457 mm 处有限位；天平防风罩门一动，稳定判断就要重新开始；哪些量策略可以观测，哪些是只留给评测的隐藏真值。ChemLab-Assets 把这些知识和几何一起打包发布。

每个资产对应一台具体的商用仪器，选自一份覆盖大学化学教学与科研常用的 200 种仪器与模块的调研，依据公开的样本、手册和论文建模。模型复现的是公开资料记载的行为，不是厂家 CAD，也没有用实物做过标定。

本仓库存放代码（运行层、测试、示例、合同校验器）、目录表、项目网站（`docs/`）和 3D 浏览器（`explorer/`）；模型与数据（约 1.5 GB）放在 [Hugging Face 数据集](https://huggingface.co/datasets/MBZUAI/ChemLab-Assets)，运行 `python tools/download_assets.py` 即可下载到 `assets/`。

## 数据一览

| | |
|---|---|
| **仪器** | 198 台商用型号，来自 115 家厂商，分属 16 个能力域 |
| **几何** | GLB、带碰撞代理的 URDF、14,492 个分件 OBJ、Blender 源文件 |
| **关节** | 1,328 个（平移 909、转动 388、连续 31）：门、移门、盖、按键、旋钮 |
| **合同** | 14,272 个组件、6,282 个带类型的状态槽、4,701 个带 JSON-Schema 参数与错误声明的能力 |
| **接口** | 648 个读数（显示屏、指示灯）和 2,348 个接口点（放置、抓取、端口） |
| **行为** | 每台一个 Python 运行层：按时间步推进的过程模型，提供 `describe` / `read` / `invoke` / `poll`，区分智能体与评测者视角，噪声可设种子 |
| **来源** | 共引用 3,382 条来源；第三方原始文档不随发布再分发 |
| **仿真** | 全部 198 台已转为 USD，并通过 Matterix 框架在 NVIDIA Isaac Lab 中运行检查 |

## 图库

<table>
<tr><td align="center" width="25%"><img src="docs/media/thumb/esco_efa5uduvw.webp" width="190" alt="Chemical fume hood"><br><sub><b>Chemical fume hood</b><br>Esco Lifesciences Frontier Acela EFA-5UDUVW-9</sub></td><td align="center" width="25%"><img src="docs/media/thumb/agilent_7890b_ssl_fid.webp" width="190" alt="Gas chromatograph (S/SL inlet + FID)"><br><sub><b>Gas chromatograph (S/SL inlet + FID)</b><br>Agilent 7890B</sub></td><td align="center" width="25%"><img src="docs/media/thumb/mt_xpr204e.webp" width="190" alt="Analytical balance"><br><sub><b>Analytical balance</b><br>METTLER TOLEDO XPR204E</sub></td><td align="center" width="25%"><img src="docs/media/thumb/brooks_preciseflex_400.webp" width="190" alt="Lab sample-transfer robot arm"><br><sub><b>Lab sample-transfer robot arm</b><br>Brooks Automation PreciseFlex 400</sub></td></tr>
<tr><td align="center" width="25%"><img src="docs/media/thumb/ika_rv10_digital_v.webp" width="190" alt="Rotary evaporator"><br><sub><b>Rotary evaporator</b><br>IKA RV 10 digital V</sub></td><td align="center" width="25%"><img src="docs/media/thumb/thermo_orbitrapexploris.webp" width="190" alt="High-resolution Orbitrap mass spectrometer"><br><sub><b>High-resolution Orbitrap mass spectrometer</b><br>Thermo Fisher Scientific Orbitrap Exploris 120</sub></td><td align="center" width="25%"><img src="docs/media/thumb/jeol_jsmit200.webp" width="190" alt="Scanning electron microscope"><br><sub><b>Scanning electron microscope</b><br>JEOL JSM-IT200 LA</sub></td><td align="center" width="25%"><img src="docs/media/thumb/buchi_lyovapor_l250_pro.webp" width="190" alt="Freeze dryer"><br><sub><b>Freeze dryer</b><br>BUCHI Lyovapor L-250 Pro</sub></td></tr>
<tr><td align="center" width="25%"><img src="docs/media/thumb/opentrons_flex_single_5_1000ul.webp" width="190" alt="Automated liquid-handling robot"><br><sub><b>Automated liquid-handling robot</b><br>Opentrons Flex (1-channel 5–1000 µL)</sub></td><td align="center" width="25%"><img src="docs/media/thumb/magritek_spinsolve80.webp" width="190" alt="Benchtop NMR spectrometer"><br><sub><b>Benchtop NMR spectrometer</b><br>Magritek Spinsolve 80 Carbon</sub></td><td align="center" width="25%"><img src="docs/media/thumb/inert_purelab_he_2gb.webp" width="190" alt="Inert-atmosphere glovebox"><br><sub><b>Inert-atmosphere glovebox</b><br>Inert PureLab HE 2GB</sub></td><td align="center" width="25%"><img src="docs/media/thumb/park_nx10.webp" width="190" alt="Atomic force microscope"><br><sub><b>Atomic force microscope</b><br>Park Systems NX10</sub></td></tr>
</table>

在 [3D 浏览器](https://huggingface.co/spaces/MBZUAI/ChemLab-Assets-Explorer) 中可以查看全部 198 台，拖动关节滑块、逐个查看零件注释。

## 按能力域分类

| 能力域 | 台数 | 举例 |
|---|---:|---|
| 热学力学与输运 | 23 | Pin-on-disk tribometer (Anton Paar TRB³); Coating scratch tester (Anton Paar RST 300); Nanoindentation tester (Anton Paar HIT 300) |
| 基础计量与样品处理 | 16 | Analytical balance (METTLER TOLEDO XPR204E); Halogen moisture analyzer (METTLER TOLEDO HX204); Oscillating U-tube density meter (Anton Paar DMA 5002) |
| 环境与公用系统 | 16 | Muffle furnace (Nabertherm L 9/11/SKM); Tube furnace (controlled atmosphere) (Carbolite Gero TF1 12/60/300); Heating/refrigerated circulator (JULABO CORIO CD-200F) |
| 萃取纯化与干燥 | 15 | Rotary evaporator (IKA RV 10 digital V); Freeze dryer (BUCHI Lyovapor L-250 Pro); Lab spray dryer (BUCHI Mini Spray Dryer S-300) |
| 颗粒孔隙与界面 | 15 | Solid-surface streaming-potential analyzer (Anton Paar SurPASS 3); Gas pycnometer (true density) (Anton Paar Ultrapyc 5000); Gas physisorption surface-area & pore analyzer (Anton Paar Autosorb 6100) |
| 分子光谱与结构 | 14 | Circular dichroism spectrometer (JASCO J-1500); Time-domain NMR relaxometer (Bruker minispec mq20); Spectrofluorometer (JASCO FP-8350) |
| 消解与固体前处理 | 13 | Graphite block digestion system (SCP SCIENCE DigiPREP MS); Planetary ball mill (RETSCH PM 100); Vibratory sieve shaker (RETSCH AS 200 control) |
| 薄膜与功能材料制备 | 13 | Vibrating-nozzle encapsulator (BUCHI Encapsulator B-390); Spin coater (Ossila Spin Coater V3); Dip coater (Ossila Dip Coater V2) |
| 元素与质谱分析 | 12 | Organic elemental (CHNS) analyzer (Elementar vario EL cube); Total organic carbon analyzer (Shimadzu TOC-L CSH); Atomic absorption spectrometer (Analytik Jena contrAA 800 D) |
| 显微成像与衍射 | 12 | Scanning electron microscope (JEOL JSM-IT200 LA); Transmission electron microscope (JEOL JEM-1400Flash); Small-angle X-ray scattering system (Anton Paar SAXSpace) |
| 生化与湿化学分析 | 11 | Microplate washer (Agilent BioTek 405 TS (96-tube manifold)); Gel electrophoresis system (Bio-Rad Mini-PROTEAN Tetra + power supply); Gel & blot imager (Thermo Fisher Scientific iBright FL1500) |
| 自动化与气液供给 | 11 | Automated powder dosing system (METTLER TOLEDO Quantos Q2 + XPE206DR); Automated liquid-handling robot (Opentrons Flex (1-channel 5–1000 µL)); Automated microplate heat sealer (Azenta Life Sciences Automated Roll Heat Sealer (a4S)) |
| 反应与分子合成 | 9 | Microwave synthesis reactor (Anton Paar Monowave 400); Electro-organic synthesis system (IKA ElectraSyn 2.0); Photochemical reactor (HepatoChem PhotoRedOx Box UV-Vis) |
| 电化学与定量分析 | 7 | Automatic potentiometric titrator (Metrohm OMNIS Professional Titrator); Karl Fischer coulometer (Metrohm 899 Coulometer); Potentiostat / electrochemical workstation (Gamry Reference 620) |
| 色谱与电泳分离 | 6 | Gas chromatograph (Agilent 8890B GC); HPLC system (Agilent 1260 Infinity III LC); Ion chromatograph (Thermo Fisher Scientific Dionex Integrion HPIC) |
| 分析进样与接口 | 5 | Headspace autosampler (48-vial tray) (Agilent 8697); Purge-and-trap autosampler (Teledyne Tekmar Atomx XYZ); Laser ablation sampling system (Teledyne CETAC Analyte Excite+) |

<details>
<summary><b>全部 198 台仪器</b></summary>

| 编号 | 仪器 | 厂家 | 型号 | 能力域 | 关节 | 状态槽 | 能力 |
|---|---|---|---|---|---:|---:|---:|
| CHEM-001 | CHEM-001 分析天平 | METTLER TOLEDO | XPR204E | 基础计量与样品处理 | 16 | 22 | 9 |
| CHEM-002 | METTLER TOLEDO HX204 卤素水分测定仪 | METTLER TOLEDO | HX204 | 基础计量与样品处理 | 5 | 14 | 8 |
| CHEM-003 | Anton Paar DMA 5002振动管液体密度计 | Anton Paar | DMA 5002 | 基础计量与样品处理 | 4 | 17 | 9 |
| CHEM-004 | Anton Paar Abbemat Advanced 5201数字折射仪 | Anton Paar | Abbemat Advanced 5201 | 基础计量与样品处理 | 5 | 19 | 10 |
| CHEM-005 | CHEM-005 多参数电化学水质测定仪 — Metrohm 914 pH/DO | Metrohm | 914 pH/DO/Conductometer | 基础计量与样品处理 | 3 | 44 | 14 |
| CHEM-006 | METTLER TOLEDO MP70自动熔点仪 | METTLER TOLEDO | MP70 | 基础计量与样品处理 | 4 | 26 | 10 |
| CHEM-007 | AQUALAB 4TE水活度仪 | AQUALAB | 4TE | 基础计量与样品处理 | 10 | 27 | 13 |
| CHEM-008 | 磁力加热搅拌器IKA Plate (RCT digital) | IKA | Plate RCT digital | 基础计量与样品处理 | 8 | 33 | 33 |
| CHEM-009 | IKA KS 4000 i control恒温轨道摇床 | IKA | KS 4000 i control | 基础计量与样品处理 | 14 | 33 | 14 |
| CHEM-010 | CHEM-010 冷冻多用途离心机 Eppendorf 5910 Ri | Eppendorf | 5910 Ri | 基础计量与样品处理 | 2 | 23 | 15 |
| CHEM-011 | 强制对流干燥箱BINDER FD 56 | BINDER | FD 56 | 基础计量与样品处理 | 7 | 25 | 32 |
| CHEM-012 | BINDER VD 56 真空干燥箱 | BINDER | VD 56 | 基础计量与样品处理 | 7 | 10 | 6 |
| CHEM-013 | Elma Elmasonic S60H原版超声清洗槽 | Elma | Elmasonic S60H | 基础计量与样品处理 | 8 | 56 | 15 |
| CHEM-014 | Hach TL2300 EPA钨灯台式浊度仪 | Hach | TL2300 EPA | 基础计量与样品处理 | 2 | 26 | 11 |
| CHEM-015 | Anton Paar Monowave 400微波合成反应器 | Anton Paar | Monowave 400 | 反应与分子合成 | 3 | 32 | 13 |
| CHEM-016 | IKA ElectraSyn 2.0 Package电有机合成仪 | IKA | ElectraSyn 2.0 | 反应与分子合成 | 2 | 40 | 12 |
| CHEM-017 | HepatoChem PhotoRedOx Box UV-Vis HCK1022 | HepatoChem | PhotoRedOx Box UV-Vis | 反应与分子合成 | 3 | 36 | 15 |
| CHEM-018 | 微型高压搅拌反应釜 Parr Instrument 4591 | Parr Instrument | 4591 | 反应与分子合成 | 9 | 22 | 10 |
| CHEM-019 | Syrris Asia Discovery Regular 连续流化学合成系统 | Syrris | Asia Discovery | 反应与分子合成 | 4 | 38 | 13 |
| CHEM-020 | ThalesNano H-Cube Pro 连续流催化加氢仪 | ThalesNano | H-Cube Pro | 反应与分子合成 | 5 | 41 | 17 |
| CHEM-021 | 独立温区并行反应工作站 Radleys Mya 4 | Radleys | Mya 4 | 反应与分子合成 | 4 | 92 | 15 |
| CHEM-022 | CEM Liberty Blue 2.0 自动固相多肽合成仪 | CEM | Liberty Blue 2.0 | 反应与分子合成 | 2 | 34 | 13 |
| CHEM-023 | Cytiva ÄKTA oligosynt 寡核苷酸合成仪 | Cytiva | ÄKTA oligosynt | 反应与分子合成 | 3 | 31 | 12 |
| CHEM-024 | IKA RV 10 digital V 旋转蒸发仪 | IKA | RV 10 digital V | 萃取纯化与干燥 | 5 | 26 | 12 |
| CHEM-025 | 冷冻干燥机 BUCHI Lyovapor L-250 Pro | BUCHI | Lyovapor L-250 Pro | 萃取纯化与干燥 | 3 | 34 | 15 |
| CHEM-026 | BUCHI Mini Spray Dryer S-300 实验室喷雾干燥机 | BUCHI | Mini Spray Dryer S-300 | 萃取纯化与干燥 | 2 | 26 | 16 |
| CHEM-027 | Tousimis Autosamdri-815 Series B 8779C 临 | Tousimis | Autosamdri-815 Series B | 萃取纯化与干燥 | 2 | 30 | 13 |
| CHEM-028 | BUCHI SpeedExtractor E-916 六位四溶剂加压萃取仪 | BUCHI | SpeedExtractor E-916 | 萃取纯化与干燥 | 4 | 33 | 14 |
| CHEM-029 | Gilson CPC250与PLC2050液液分配纯化系统 | Gilson | CPC 250 | 萃取纯化与干燥 | 3 | 28 | 10 |
| CHEM-030 | Sartorius Vivaflow200单盒切向流超滤组件 | Sartorius | Vivaflow 200 | 萃取纯化与干燥 | 1 | 33 | 10 |
| CHEM-031 | Biotage Extrahera Classic 自动固相萃取工作站 | Biotage | Extrahera Classic | 萃取纯化与干燥 | 0 | 30 | 12 |
| CHEM-032 | SFT110超临界流体萃取仪100mL配置 | Supercritical Fluid Technologies | SFT-110 | 萃取纯化与干燥 | 3 | 30 | 13 |
| CHEM-033 | Asia FLLEX连续流液液萃取模块 | Syrris | Asia FLLEX | 萃取纯化与干燥 | 2 | 42 | 15 |
| CHEM-034 | Biotage Selekt双通道单床UV快速制备色谱仪 | Biotage | Selekt (UV, single bed) | 萃取纯化与干燥 | 3 | 55 | 15 |
| CHEM-035 | PCCell ED64002 / BED1-2 Basic实验室电渗析组件 | PCCell | ED 64002 + BED1-2 Basic | 萃取纯化与干燥 | 3 | 28 | 14 |
| CHEM-036 | Genevac miVac 2 Duo离心真空浓缩仪 Rev00101/25 | Genevac / SP Industries | miVac 2 Duo | 萃取纯化与干燥 | 9 | 22 | 13 |
| CHEM-037 | PILODIST104高效实验室精馏装置6L90板规格 | PILODIST | PILODIST 104 | 萃取纯化与干燥 | 4 | 24 | 15 |
| CHEM-038 | UIC KDL1 Basic 刮膜短程分子蒸馏装置 | UIC | KDL1 Basic | 萃取纯化与干燥 | 5 | 30 | 17 |
| CHEM-039 | SCP SCIENCE DigiPREP MS 010-500-205 石墨块消 | SCP SCIENCE | DigiPREP MS | 消解与固体前处理 | 2 | 24 | 13 |
| CHEM-040 | RETSCH PM 100 单工位行星式球磨仪 | RETSCH | PM 100 | 消解与固体前处理 | 8 | 24 | 13 |
| CHEM-041 | RETSCH AS 200 control 振动筛分仪 | RETSCH | AS 200 control | 消解与固体前处理 | 16 | 24 | 20 |
| CHEM-042 | RETSCH PT100旋转缩分仪与DR100 | RETSCH | PT 100 | 消解与固体前处理 | 15 | 25 | 21 |
| CHEM-043 | IKA T25digital转子定子分散机 | IKA | T 25 digital ULTRA-TURRAX | 消解与固体前处理 | 4 | 30 | 16 |
| CHEM-044 | Sonics VCX750探头式超声处理器 | Sonics | VCX 750 | 消解与固体前处理 | 25 | 34 | 19 |
| CHEM-045 | THINKY ARE-310S-UL行星离心混合脱泡机 | THINKY | ARE-310 | 消解与固体前处理 | 10 | 33 | 14 |
| CHEM-046 | Metrohm909UV紫外消解仪 | Metrohm | 909 UV Digester | 消解与固体前处理 | 10 | 31 | 17 |
| CHEM-047 | 微波酸消解仪 Anton Paar Multiwave 7101 | Anton Paar | Multiwave 7101 | 消解与固体前处理 | 1 | 16 | 14 |
| CHEM-048 | VELP UDK159自动蒸汽蒸馏滴定仪 | VELP | UDK 159 | 消解与固体前处理 | 6 | 34 | 18 |
| CHEM-049 | BUCHI HydrolEx H-506酸水解前处理仪 | BUCHI | HydrolEx H-506 | 消解与固体前处理 | 10 | 25 | 19 |
| CHEM-050 | Malvern Panalytical Claisse LeNeo自动熔融制样仪 | Malvern Panalytical / Claisse | LeNeo | 消解与固体前处理 | 6 | 20 | 15 |
| CHEM-051 | Specac Atlas Manual15T液压粉末压片机 | Specac | Atlas Manual 15T | 消解与固体前处理 | 9 | 27 | 16 |
| CHEM-052 | BUCHI Encapsulator B-390振动喷嘴微胶囊制备仪 | BUCHI | Encapsulator B-390 | 薄膜与功能材料制备 | 8 | 30 | 23 |
| CHEM-053 | Ossila Spin Coater Standard V3 L2001A3旋涂 | Ossila | Spin Coater V3 | 薄膜与功能材料制备 | 15 | 13 | 14 |
| CHEM-054 | Ossila Dip Coater V2 L2006A2 | Ossila | Dip Coater V2 | 薄膜与功能材料制备 | 15 | 18 | 19 |
| CHEM-055 | Ossila Doctor Blade Coater | Ossila | Doctor Blade Coater | 薄膜与功能材料制备 | 16 | 21 | 20 |
| CHEM-056 | Ossila Slot-Die Coater V2 L2005A2 | Ossila | Slot-Die Coater V2 | 薄膜与功能材料制备 | 17 | 29 | 24 |
| CHEM-057 | Thermo Scientific Process11 | Thermo Fisher Scientific | Process 11 | 薄膜与功能材料制备 | 5 | 32 | 24 |
| CHEM-058 | FUJIFILM Dimatix DMP-2850 S | FUJIFILM Dimatix | DMP-2850 | 薄膜与功能材料制备 | 4 | 28 | 23 |
| CHEM-059 | Linari Nanotech RT Advanced静电纺丝系统 | Linari Nanotech | RT Advanced | 薄膜与功能材料制备 | 14 | 33 | 21 |
| CHEM-060 | NanoAssemblr Ignite NIN0001微流控制备仪 | Cytiva / Precision NanoSystems | NanoAssemblr Ignite | 薄膜与功能材料制备 | 9 | 35 | 20 |
| CHEM-061 | Glatt Mini-Glatt2012流化床制粒包衣仪 | Glatt | Mini-Glatt | 薄膜与功能材料制备 | 12 | 33 | 25 |
| CHEM-062 | Quorum Q150T ES Plus显微样品镀膜机 | Quorum Technologies | Q150T ES Plus | 薄膜与功能材料制备 | 7 | 28 | 18 |
| CHEM-063 | LABOAO CVD-LTF12P50单温区化学气相沉积系统 | LABOAO | CVD-LTF12P50 | 薄膜与功能材料制备 | 10 | 34 | 16 |
| CHEM-064 | Arradiance GEMStar XT-SP单歧管PEALD/热ALD系统 | Arradiance | GEMStar XT-SP (PEALD / thermal ALD) | 薄膜与功能材料制备 | 8 | 31 | 21 |
| CHEM-065 | JASCO J-1500 150W标准PMT电子圆二色光谱仪 | JASCO | J-1500 | 分子光谱与结构 | 2 | 25 | 18 |
| CHEM-066 | Bruker minispec mq20时域核磁弛豫分析仪 | Bruker | minispec mq20 | 分子光谱与结构 | 1 | 21 | 17 |
| CHEM-067 | JASCO FP-8350 荧光分光光度计 | JASCO | FP-8350 | 分子光谱与结构 | 2 | 25 | 19 |
| CHEM-068 | 核磁共振结构光谱仪 — Magritek Spinsolve 80 Carbon | Magritek | Spinsolve 80 Carbon | 分子光谱与结构 | 1 | 24 | 17 |
| CHEM-069 | Bruker Magnettech ESR5000 电子顺磁共振谱仪 | Bruker | Magnettech ESR5000 | 分子光谱与结构 | 1 | 21 | 19 |
| CHEM-070 | JASCO P-4200 数字旋光仪 | JASCO | P-4200 | 分子光谱与结构 | 2 | 23 | 20 |
| CHEM-071 | Hach DR6000 紫外可见分光光度计 | Hach | DR6000 | 分子光谱与结构 | 1 | 14 | 14 |
| CHEM-072 | JASCO FT/IR-4700 傅里叶变换红外光谱仪 | JASCO | FT/IR-4700 | 分子光谱与结构 | 1 | 18 | 13 |
| CHEM-073 | JASCO NRS-5500 显微拉曼光谱仪 | JASCO | NRS-5500 | 分子光谱与结构 | 4 | 26 | 23 |
| CHEM-074 | Applied Photophysics SX20 停流反应动力学光谱仪 | Applied Photophysics | SX20 | 分子光谱与结构 | 6 | 30 | 19 |
| CHEM-075 | Edinburgh Instruments FLS1000 TCSPC 时间分辨 | Edinburgh Instruments | FLS1000 | 分子光谱与结构 | 4 | 26 | 18 |
| CHEM-076 | Ultrafast Systems HELIOS 飞秒瞬态吸收光谱仪 | Ultrafast Systems | HELIOS | 分子光谱与结构 | 4 | 25 | 17 |
| CHEM-077 | JASCO FVS-6000 振动圆二色光谱仪 | JASCO | FVS-6000 | 分子光谱与结构 | 4 | 32 | 22 |
| CHEM-078 | JASCO CPL-300 圆偏振发光光谱仪 | JASCO | CPL-300 | 分子光谱与结构 | 5 | 29 | 22 |
| CHEM-079 | Elementar vario EL cube 有机元素分析仪 | Elementar | vario EL cube | 元素与质谱分析 | 5 | 31 | 23 |
| CHEM-080 | Shimadzu TOC-L CSH 总有机碳分析仪 | Shimadzu | TOC-L CSH | 元素与质谱分析 | 40 | 32 | 29 |
| CHEM-081 | Analytik Jena contrAA 800 D 原子吸收光谱仪 | Analytik Jena | contrAA 800 D | 元素与质谱分析 | 10 | 41 | 33 |
| CHEM-082 | Shimadzu ICPE-9820 电感耦合等离子体发射光谱仪 | Shimadzu | ICPE-9800 | 元素与质谱分析 | 3 | 36 | 33 |
| CHEM-083 | Shimadzu ICPMS-2030 电感耦合等离子体质谱仪 | Shimadzu | ICPMS-2030 | 元素与质谱分析 | 5 | 37 | 30 |
| CHEM-084 | Shimadzu EDX-7200 能量色散X射线荧光分析仪 | Shimadzu | EDX-7200 | 元素与质谱分析 | 3 | 33 | 29 |
| CHEM-085 | Analytik Jena multi X 2500 总有机卤素分析仪 | Analytik Jena | multi X 2500 | 元素与质谱分析 | 8 | 38 | 37 |
| CHEM-086 | Milestone DMA-80 evo DoubleBeam 直接测汞仪 | Milestone | DMA-80 evo | 元素与质谱分析 | 5 | 38 | 34 |
| CHEM-087 | Stanford Research Systems RGA100 残余气体质谱分 | Stanford Research Systems | RGA100 | 元素与质谱分析 | 2 | 40 | 34 |
| CHEM-088 | Thermo Fisher Scientific Orbitrap Explor | Thermo Fisher Scientific | Orbitrap Exploris 120 | 元素与质谱分析 | 4 | 37 | 37 |
| CHEM-089 | LECO ONH836 惰气熔融氧氮氢分析仪 | LECO | ONH836 | 元素与质谱分析 | 4 | 36 | 35 |
| CHEM-090 | 稳定同位素比质谱仪Elementar isoprime precisION | Elementar | isoprime precisION | 元素与质谱分析 | 2 | 21 | 30 |
| CHEM-096 | Agilent 8890B GC 气相色谱仪 | Agilent | 8890B GC | 色谱与电泳分离 | 50 | 22 | 56 |
| CHEM-097 | 高效液相色谱仪 Agilent | 1260 Infinity III LC | Agilent | 1260 Infinity III LC | 色谱与电泳分离 | 1 | 24 | 15 |
| CHEM-098 | Thermo Scientific Dionex Integrion HPIC  | Thermo Fisher Scientific | Dionex Integrion HPIC | 色谱与电泳分离 | 2 | 21 | 16 |
| CHEM-099 | Agilent 1290 Infinity II GPC/SEC 凝胶渗透体积排 | Agilent | 1290 Infinity II GPC/SEC | 色谱与电泳分离 | 3 | 25 | 18 |
| CHEM-100 | G.A.S. FlavourSpec25 GC-IMS 离子迁移谱正交分离系统 | G.A.S. | FlavourSpec25 GC-IMS | 色谱与电泳分离 | 4 | 29 | 20 |
| CHEM-101 | Metrohm OMNIS Professional Titrator 2.10 | Metrohm | OMNIS Professional Titrator | 电化学与定量分析 | 2 | 34 | 15 |
| CHEM-102 | CHEM-102 卡尔费休水分滴定仪 · Metrohm 899 Coulome | Metrohm | 899 Coulometer | 电化学与定量分析 | 11 | 26 | 10 |
| CHEM-103 | Gamry Reference 620 电化学工作站 | Gamry | Reference 620 | 电化学与定量分析 | 0 | 21 | 10 |
| CHEM-104 | CH Instruments CHI920D 扫描电化学显微镜 | CH Instruments | CHI920D | 电化学与定量分析 | 3 | 29 | 16 |
| CHEM-105 | CH Instruments CHI440C 电化学石英晶体微天平 | CH Instruments | CHI440C | 电化学与定量分析 | 1 | 19 | 17 |
| CHEM-106 | KNAUER K-7400S 冰点渗透压仪 | KNAUER | K-7400S | 电化学与定量分析 | 2 | 24 | 17 |
| CHEM-107 | Pine Research WaveVortex 10旋转环盘驱动 | Pine Research | WaveVortex 10 | 电化学与定量分析 | 4 | 30 | 22 |
| CHEM-108 | CHEM-108 Agilent 8697 顶空自动进样器 | Agilent | 8697 | 分析进样与接口 | 1 | 24 | 14 |
| CHEM-109 | Teledyne Tekmar Atomx XYZ 吹扫捕集自动进样系统 | Teledyne Tekmar | Atomx XYZ | 分析进样与接口 | 7 | 25 | 20 |
| CHEM-110 | Teledyne CETAC Analyte Excite+ 激光剥蚀系统 | Teledyne CETAC | Analyte Excite+ | 分析进样与接口 | 6 | 22 | 23 |
| CHEM-111 | Frontier EGA/PY-3030D 热裂解进样器 | Frontier Laboratories | EGA/PY-3030D | 分析进样与接口 | 4 | 22 | 22 |
| CHEM-112 | Markes UNITY-xr 热脱附进样器 | Markes International | UNITY-xr | 分析进样与接口 | 6 | 27 | 26 |
| CHEM-113 | Anton Paar SurPASS 3 固体表面流动电位分析仪 | Anton Paar | SurPASS 3 | 颗粒孔隙与界面 | 7 | 29 | 26 |
| CHEM-114 | Anton Paar Ultrapyc 5000 气体置换真密度仪 | Anton Paar | Ultrapyc 5000 | 颗粒孔隙与界面 | 6 | 33 | 25 |
| CHEM-115 | Anton Paar Autosorb 6100 气体物理吸附比表面积与孔径分析 | Anton Paar | Autosorb 6100 | 颗粒孔隙与界面 | 8 | 49 | 33 |
| CHEM-116 | Anton Paar PoreMaster 60 标准型压汞孔隙率分析仪 | Anton Paar | PoreMaster 60 | 颗粒孔隙与界面 | 7 | 27 | 28 |
| CHEM-117 | Anton Paar Litesizer DLS 701 动态光散射与胶体电位仪 | Anton Paar | Litesizer DLS 701 | 颗粒孔隙与界面 | 6 | 31 | 29 |
| CHEM-118 | Anton Paar PSA1190 L/D 激光衍射粒度仪 | Anton Paar | PSA 1190 | 颗粒孔隙与界面 | 9 | 31 | 28 |
| CHEM-119 | Anton Paar Litesizer DIA700 Liquid Flow  | Anton Paar | Litesizer DIA 700 | 颗粒孔隙与界面 | 7 | 31 | 29 |
| CHEM-120 | Biolin Scientific Sigma700 力学式表面张力仪 | Biolin Scientific | Sigma 700 | 颗粒孔隙与界面 | 7 | 30 | 32 |
| CHEM-121 | Biolin Scientific Theta Flex 光学接触角测量仪 | Biolin Scientific | Theta Flex | 颗粒孔隙与界面 | 8 | 33 | 38 |
| CHEM-122 | Micromeritics AutoChem III 程序升温化学吸附仪 | Micromeritics | AutoChem III | 颗粒孔隙与界面 | 6 | 36 | 37 |
| CHEM-123 | Surface Measurement Systems DVS Resoluti | Surface Measurement Systems | DVS Resolution | 颗粒孔隙与界面 | 6 | 40 | 37 |
| CHEM-124 | Freeman Technology / Micromeritics FT4 P | Freeman Technology / Micromeritics | FT4 Powder Rheometer | 颗粒孔隙与界面 | 6 | 39 | 33 |
| CHEM-125 | POROMETER POROLUX Revo 毛细流动孔径仪 | POROMETER | POROLUX Revo | 颗粒孔隙与界面 | 3 | 28 | 34 |
| CHEM-126 | Microtrac TURBISCAN LAB 分散体系稳定性分析仪 | Microtrac | TURBISCAN LAB | 颗粒孔隙与界面 | 5 | 28 | 29 |
| CHEM-127 | KSV NIMA Medium L&LB Langmuir 单分子膜槽 | Biolin Scientific KSV NIMA | Medium L&LB Trough | 颗粒孔隙与界面 | 7 | 36 | 34 |
| CHEM-128 | Anton Paar TRB³ 销盘摩擦磨损试验机 | Anton Paar | TRB³ | 热学力学与输运 | 6 | 42 | 39 |
| CHEM-129 | Anton Paar RST 300 涂层划痕附着力试验机 | Anton Paar | RST 300 | 热学力学与输运 | 6 | 44 | 39 |
| CHEM-130 | Anton Paar HIT 300 纳米压痕仪 | Anton Paar | HIT 300 | 热学力学与输运 | 6 | 38 | 33 |
| CHEM-131 | TA Instruments DSC 2500 差示扫描量热仪 | TA Instruments | DSC 2500 | 热学力学与输运 | 5 | 43 | 36 |
| CHEM-132 | TA Instruments TGA 5500 热重分析仪 | TA Instruments | TGA 5500 | 热学力学与输运 | 4 | 48 | 40 |
| CHEM-133 | TA Instruments DMA 850 动态机械分析仪 | TA Instruments | DMA 850 | 热学力学与输运 | 5 | 43 | 41 |
| CHEM-134 | TA Instruments TMA 450 热机械分析仪 | TA Instruments | TMA 450 | 热学力学与输运 | 3 | 50 | 47 |
| CHEM-135 | NETZSCH LFA467 HyperFlash 闪光法热扩散仪 | NETZSCH | LFA 467 HyperFlash | 热学力学与输运 | 4 | 54 | 47 |
| CHEM-136 | Hot Disk TPS 2500 S 瞬态平面热源导热仪 | Hot Disk | TPS 2500 S | 热学力学与输运 | 4 | 25 | 25 |
| CHEM-137 | Anton Paar MCR 503 旋转与振荡流变仪 | Anton Paar | MCR 503 | 热学力学与输运 | 3 | 26 | 28 |
| CHEM-138 | TA Instruments TAM IV 20 mL 等温微量热仪 | TA Instruments | TAM IV | 热学力学与输运 | 2 | 25 | 31 |
| CHEM-139 | Linseis LSR-3 红外炉800°C热电性能测试仪 | Linseis | LSR-3 | 热学力学与输运 | 5 | 32 | 31 |
| CHEM-140 | Novocontrol Alpha-T Rev C 宽频介电谱仪 | Novocontrol Technologies | Alpha-T | 热学力学与输运 | 3 | 27 | 30 |
| CHEM-141 | Lake Shore MeasureReady M91 FastHall 霍尔测 | Lake Shore Cryotronics | MeasureReady M91 FastHall | 热学力学与输运 | 4 | 36 | 31 |
| CHEM-142 | Signatone QuadPro2-DAC0 自动四探针面电阻测绘系统 | Signatone | QuadPro2 | 热学力学与输运 | 5 | 34 | 29 |
| CHEM-143 | Lake Shore 8604 振动样品磁强计 | Lake Shore Cryotronics | 8604 VSM | 热学力学与输运 | 5 | 34 | 32 |
| CHEM-144 | Shimadzu AGS-10kNXD 万能材料试验机 | Shimadzu | AGS-10kNXD | 热学力学与输运 | 6 | 35 | 28 |
| CHEM-145 | ZwickRoell HIT5P 摆锤冲击试验机 | ZwickRoell | HIT5P | 热学力学与输运 | 4 | 31 | 27 |
| CHEM-146 | ZwickRoell Mflow 熔体流动速率仪 | ZwickRoell | Mflow | 热学力学与输运 | 9 | 43 | 36 |
| CHEM-147 | AMETEK MOCON OX-TRAN 2/22 L 氧气透过率分析仪 | AMETEK MOCON | OX-TRAN 2/22 L | 热学力学与输运 | 8 | 55 | 31 |
| CHEM-148 | NETZSCH MMC 315 Nexus 多模块量热仪 | NETZSCH | MMC 315 Nexus | 热学力学与输运 | 3 | 30 | 24 |
| CHEM-149 | METTLER TOLEDO RC1mx + HFCal 热流式反应量热系统 | METTLER TOLEDO | RC1mx + HFCal | 热学力学与输运 | 5 | 57 | 37 |
| CHEM-150 | IKA C 6000 isoperibol 氧弹量热仪 | IKA | C 6000 isoperibol | 热学力学与输运 | 3 | 43 | 44 |
| CHEM-151 | JEOL JSM-IT200 LA 扫描电子显微镜 | JEOL | JSM-IT200 LA | 显微成像与衍射 | 12 | 64 | 48 |
| CHEM-152 | JEOL JEM-1400Flash HR 透射电子显微镜 | JEOL | JEM-1400Flash | 显微成像与衍射 | 21 | 77 | 59 |
| CHEM-153 | Anton Paar SAXSpace 小角/广角X射线散射仪 | Anton Paar | SAXSpace | 显微成像与衍射 | 13 | 76 | 54 |
| CHEM-154 | Kratos / Shimadzu AXIS Supra+ 成像X射线光电子能谱 | Kratos / Shimadzu | AXIS Supra+ | 显微成像与衍射 | 10 | 67 | 49 |
| CHEM-155 | KEYENCE VK-X3000三维光学表面轮廓显微镜 | KEYENCE | VK-X3000 / VK-X3100 | 显微成像与衍射 | 7 | 51 | 38 |
| CHEM-156 | Bruker X4 POSEIDON台式三维X射线显微镜/微米CT | Bruker | X4 POSEIDON | 显微成像与衍射 | 7 | 60 | 46 |
| CHEM-157 | J.A. Woollam alpha-SE光谱椭偏仪 | J.A. Woollam | alpha-SE | 显微成像与衍射 | 5 | 39 | 37 |
| CHEM-158 | Park Systems NX10原子力显微镜 | Park Systems | NX10 | 显微成像与衍射 | 12 | 54 | 56 |
| CHEM-159 | Nanosurf NaioSTM扫描隧道显微镜 | Nanosurf | NaioSTM | 显微成像与衍射 | 3 | 36 | 52 |
| CHEM-160 | Rigaku MiniFlex600 粉末X射线衍射仪 | Rigaku | MiniFlex600 | 显微成像与衍射 | 3 | 25 | 14 |
| CHEM-161 | Rigaku XtaLAB Synergy-R单晶X射线衍射仪 | Rigaku Oxford Diffraction | XtaLAB Synergy-R | 显微成像与衍射 | 11 | 44 | 55 |
| CHEM-162 | Evident / Olympus BX53-P偏光显微镜 | Evident / Olympus | BX53-P | 显微成像与衍射 | 14 | 47 | 34 |
| CHEM-163 | Agilent BioTek 405 TS微孔板洗板机 | Agilent BioTek | 405 TS (96-tube manifold) | 生化与湿化学分析 | 3 | 36 | 26 |
| CHEM-164 | Bio-Rad Mini-PROTEAN Tetra垂直电泳槽 | Bio-Rad | Mini-PROTEAN Tetra + power supply | 生化与湿化学分析 | 8 | 36 | 37 |
| CHEM-165 | Thermo Fisher Invitrogen iBright FL1500凝 | Thermo Fisher Scientific | iBright FL1500 | 生化与湿化学分析 | 3 | 24 | 32 |
| CHEM-166 | Eppendorf CellXpert C170i CO2细胞培养箱 | Eppendorf | CellXpert C170i | 生化与湿化学分析 | 5 | 57 | 46 |
| CHEM-167 | Bio-Rad CFX Opus 96实时荧光定量PCR仪 | Bio-Rad | CFX Opus 96 | 生化与湿化学分析 | 3 | 40 | 35 |
| CHEM-168 | BD FACSAria III流式细胞分选仪 | BD Biosciences | FACSAria III | 生化与湿化学分析 | 10 | 105 | 69 |
| CHEM-169 | MicroCal PEAQ-ITC等温滴定量热仪 | Malvern Panalytical | MicroCal PEAQ-ITC | 生化与湿化学分析 | 5 | 58 | 45 |
| CHEM-170 | Biacore T200表面等离子共振生物传感器 | Cytiva | Biacore T200 | 生化与湿化学分析 | 7 | 66 | 64 |
| CHEM-171 | 生化需氧量呼吸测定系统WTW OxiTop-i IS 6 | WTW (Xylem) | OxiTop-i | 生化与湿化学分析 | 30 | 45 | 33 |
| CHEM-172 | 分段连续流动分析仪SEAL AA500 | SEAL Analytical | AA500 | 生化与湿化学分析 | 11 | 29 | 40 |
| CHEM-173 | 多模式微孔板读板仪BMG LABTECH CLARIOstar Plus | BMG LABTECH | CLARIOstar Plus | 生化与湿化学分析 | 5 | 22 | 37 |
| CHEM-174 | 自动粉末定量分配仪 — METTLER TOLEDO Quantos Q2 +  | METTLER TOLEDO | Quantos Q2 + XPE206DR | 自动化与气液供给 | 3 | 20 | 12 |
| CHEM-175 | 自动移液工作站 — Opentrons Flex 单通道5–1000 µL | Opentrons | Flex (1-channel 5–1000 µL) | 自动化与气液供给 | 4 | 15 | 10 |
| CHEM-176 | 自动微孔板热封膜机 | Azenta Life Sciences | Automated Roll Heat Sealer (a4S) | 自动化与气液供给 | 1 | 22 | 9 |
| CHEM-177 | 自动开盖封盖机 | Azenta Life Sciences | IntelliXcap S1 46-0001 | 自动化与气液供给 | 8 | 13 | 6 |
| CHEM-178 | 实验室样品转运机械臂 — Brooks Automation PreciseFl | Brooks Automation | PreciseFlex 400 | 自动化与气液供给 | 4 | 15 | 9 |
| CHEM-179 | 样品管二维条码读架仪 | Azenta Ziath | DP5 Mirage | 自动化与气液供给 | 1 | 16 | 7 |
| CHEM-180 | 气体质量流量控制器 | Alicat Scientific | MC, 1 SLPM full scale, Rev6 | 自动化与气液供给 | 7 | 25 | 11 |
| CHEM-181 | 电子背压控制器 | Bronkhorst | EL-PRESS P-702CV | 自动化与气液供给 | 1 | 18 | 9 |
| CHEM-182 | 多路气体自动切换阀 — VICI Valco EUTA-2SD6MWE | VICI Valco | EUTA-2SD6MWE | 自动化与气液供给 | 1 | 8 | 3 |
| CHEM-183 | 受控蒸发与混气进料器Bronkhorst CEM W-202A-NN0-K | Bronkhorst | CEM W-202A | 自动化与气液供给 | 1 | 20 | 21 |
| CHEM-184 | 可编程注射泵New Era NE-1000 Multi-Phaser | New Era Pump Systems | NE-1000 Multi-Phaser | 自动化与气液供给 | 12 | 31 | 35 |
| CHEM-185 | Nabertherm L 9/11/SKM 马弗炉 | Nabertherm | L 9/11/SKM | 环境与公用系统 | 7 | 12 | 10 |
| CHEM-186 | 管式气氛炉 Carbolite Gero TF1 12/60/300 | Carbolite Gero | TF1 12/60/300 | 环境与公用系统 | 6 | 14 | 5 |
| CHEM-187 | JULABO CORIO CD-200F 9012701.N1.03 加热制冷循 | JULABO | CORIO CD-200F | 环境与公用系统 | 6 | 15 | 4 |
| CHEM-188 | 化学耐腐蚀真空工作站 | VACUUBRAND | PC 3001 VARIO select | 环境与公用系统 | 3 | 12 | 7 |
| CHEM-189 | Inert PureLab HE 2GB 惰性气氛手套箱 | Inert | PureLab HE 2GB | 环境与公用系统 | 2 | 27 | 10 |
| CHEM-190 | 实验室超纯水系统 | ELGA LabWater | PURELAB flex 3 | 环境与公用系统 | 6 | 26 | 8 |
| CHEM-191 | Miele PLW 6111 DP SC CL H14 10958380 实验室 | Miele | PLW 6111 | 环境与公用系统 | 7 | 20 | 8 |
| CHEM-192 | 立式高压蒸汽灭菌器Tuttnauer 3870ELV-D | Tuttnauer | 3870ELV-D | 环境与公用系统 | 5 | 19 | 24 |
| CHEM-193 | 生物安全柜Esco Airstream Plus AC2-4S8-TU | Esco Lifesciences | AC2-4S8-TU | 环境与公用系统 | 9 | 30 | 24 |
| CHEM-194 | 氦质谱检漏仪INFICON UL1000 Fab | INFICON | UL1000 Fab | 环境与公用系统 | 6 | 28 | 26 |
| CHEM-195 | 微量水露点变送器Vaisala DRYCAP DMT152 | Vaisala | DMT152 | 环境与公用系统 | 1 | 25 | 21 |
| CHEM-196 | 实验室氢气发生器PEAK Precision Hydrogen Trace 50 | PEAK Scientific | Precision Hydrogen Trace 500 | 环境与公用系统 | 2 | 23 | 21 |
| CHEM-197 | 超低温冰箱Eppendorf CryoCube F570n | Eppendorf | CryoCube F570n | 环境与公用系统 | 12 | 35 | 34 |
| CHEM-198 | 恒温恒湿试验箱BINDER KBF 115 | BINDER | KBF 115 | 环境与公用系统 | 3 | 33 | 33 |
| CHEM-199 | 台式化学通风柜Esco Frontier Acela EFA-5UDUVW-9 | Esco Lifesciences | Frontier Acela EFA-5UDUVW-9 | 环境与公用系统 | 11 | 32 | 32 |
| CHEM-200 | 溶剂纯化系统Inert PureSolv MD 7 | Inert | PureSolv MD 7 | 环境与公用系统 | 27 | 67 | 23 |
| — | Agilent 7890B 气相色谱仪 | Agilent | 7890B | 色谱与电泳分离 | 50 | 0 | 16 |
| — | OHAUS Pioneer PX224/E 分析天平 | OHAUS | Pioneer PX224/E | 基础计量与样品处理 | 16 | 0 | 7 |
| — | OHAUS Scout SPX2201 电子天平 | OHAUS | Scout SPX2201 | 基础计量与样品处理 | 15 | 0 | 7 |

</details>

## 快速上手

**1. 下载数据**（全部约 1.5 GB，或只下一台加代码）：

```bash
pip install -U huggingface_hub
hf download MBZUAI/ChemLab-Assets --repo-type dataset --local-dir ChemLab-Assets
pip install -r ChemLab-Assets/requirements.txt   # NumPy 2.0–2.3, SciPy, Pillow, OpenCV, CoolProp, pytest

hf download MBZUAI/ChemLab-Assets --repo-type dataset --local-dir ChemLab-Assets \
    --include "assets/expansion_batch05/t2_vacuum/esco_efa5uduvw/*" "scripts/*" "examples/*"
```

**2. 运行一台仪器。** 下面的通风柜一开始风速监视器没有校准。机器人拉起移门，看到读数偏低并报警，于是用风速计实测开口风速，再校准监视器。真实面风速（0.299 m/s）属于评测者专用的状态槽，机器人始终看不到。完整脚本见 [`examples/quickstart_fume_hood.py`](examples/quickstart_fume_hood.py)。

```python
from asset_runtime import esco_efa5uduvw as rt   # run from the repo root with scripts/ on sys.path

hood = rt.EscoEfa5uduvwRuntime("hood_01")
hood.register_principal("lab", "evaluator")    # sets up the world, sees hidden truth
hood.register_principal("robot", "agent")      # sees what a policy may see
lab, robot = hood.session("lab"), hood.session("robot")

call(lab, "plant.configure", **rt.PLANT_EXAMPLE)        # 295.15 K, 101.325 kPa
call(lab, "exhaust.configure", **rt.EXHAUST_EXAMPLE)    # building exhaust, damper half open
call(robot, "sash.set", height_m=0.457)                  # raise to the 18-inch stop
call(lab, "sim.step", dt_s=30.0)
show("before calibration")

v = call(robot, "anemometer.measure")["face_velocity_m_s"]
call(robot, "monitor.calibrate", reference_m_s=round(v, 3))
call(lab, "sim.step", dt_s=10.0)
show("after calibration")
```

```text
before calibration   reading 0.28 m/s   alarm low
anemometer           0.309 m/s
after calibration    reading 0.31 m/s   alarm none
```

**3. 放进仿真器。** URDF 中的网格路径都是相对路径，任何 URDF 加载器都能直接用。全部 198 个 URDF 均已用 [yourdfpy](https://github.com/clemense/yourdfpy) 验证可加载：

```python
import yourdfpy
hood = yourdfpy.URDF.load("assets/expansion_batch05/t2_vacuum/esco_efa5uduvw/model.urdf")
hood.update_cfg({"sash_joint": 0.45})   # 移门拉起 450 mm
hood.show()
```

Isaac Lab 用户可用其 URDF 导入器转换 `model.urdf`。GLB 保持 Z 轴向上的资产坐标系，在 Y 轴向上的查看器里需绕 X 轴旋转 −90°（目录表的 `glb_up` 列标明了哪些文件需要）。

## 一个资产包含什么

```text
assets/expansion_batch05/t2_vacuum/esco_efa5uduvw/
├── asset.contract.json      # the contract: components, joints, state slots, capabilities, sources, limitations
├── model.glb                # viewing (asset frame: Z up, front faces −Y)
├── model.urdf               # simulators: links, joints with limits, primitive collision proxies
├── meshes/                  # one OBJ + MTL per functional part (78 files here)
├── textures/                # panel, label and screen textures drawn for the model
├── model.blend              # Blender source with named parts
├── geometry_manifest.json   # parts, frames, dimensions and joint layout
├── collision_proxies.json   # primitive colliders per part
├── instrument_model.json    # runtime parameters (this instrument)
├── vapour_templates.json    # reference data used by the runtime (53 vapour sources)
└── 说明.md                  # notes on the model and its data (Chinese)

scripts/asset_runtime/esco_efa5uduvw.py   # the behaviour runtime
tests/test_asset_esco_efa5uduvw.py        # its tests
```

资产合同是每个资产的核心：列出每个功能**组件**并绑定到网格和碰撞体；列出**状态槽**的类型、单位、范围、来源（设定值、传感器、推导值、过程真值）与可见性（智能体、评测者、调试）；定义**能力**的 JSON-Schema 参数、前置条件、状态影响、错误码，以及在过程、宿主、场景、机器人、硬件五个层级上的验证程度；并记录**机理**、**端口**、**来源**和**局限**。规范见 [`contract.md`](skills/chemistry-lab-assets/references/contract.md) 和 [`protocol.md`](skills/chemistry-lab-assets/references/protocol.md)，校验器为 `skills/chemistry-lab-assets/scripts/asset_tool.py`。

## 来源、许可与边界

* **来源**：每个状态槽和能力都写明依据。厂家资料给出尺寸、量程和操作限值；论文、标准和公开数据库给出仪器输出背后的实测数据；两者都没有时，合同明确写出假设。第三方原始文档（PDF 手册、样本、应用笔记、论文、谱图文件、照片）不再分发，合同只保留引用和派生数值。
* **许可**：代码采用 MIT（[`LICENSE`](LICENSE)）；模型、合同与数据采用 CC BY-NC 4.0（[`LICENSE-ASSETS.md`](LICENSE-ASSETS.md)）。部分数据文件沿用其来源的条款（CC BY-SA、非商用或出版方保留权利），[`NOTICE.md`](NOTICE.md) 逐一列出。
* **商标**：产品与公司名称仅用于标明所建模的仪器，本项目与任何厂商无关联，也未获其背书。
* **边界**：786 个能力已声明但标为不支持；没有任何能力声称经过机器人接触或真实硬件验证。请把这些资产当作有文档、可测试的行为模型用于具身智能研究，而不是经过认证的数字孪生。
* **构建记录**：合同中列出的部分文件属于原始构建过程（验证日志、评审记录、来源缓存），不在本次发布范围内；`release_manifest.json` 说明了哪些文件随发布提供。

## 待办清单

- [x] 所有有物质相关读数的仪器都配上真实数据输出模板（1.1）
- [x] 公共化合物表扩到 200 种，并建立化合物 ↔ 仪器对照表（1.1）
- [ ] 补齐运行层真实记录仍不足 50 条的库：Langmuir 槽（36）、Turbiscan（23）、Process 11（37）、multi X 2500（41）、FVS-6000 VCD（37）、Mini-Glatt（47）
- [ ] 把共享反应模板从 38 条扩到约 100 条，覆盖连续流、光氧化还原、电合成、多肽合成和量热类仪器
- [ ] 标定反应动力学（目前多数速率常数仍是明确标注的假设）
- [ ] 补充在对应型号上实测的原机数据（目前多数模板来自其他同类仪器）
- [ ] 机器人接触与真实硬件验证

## 引用

```bibtex
@misc{song2026chemlabassets,
  title        = {ChemLab-Assets: Simulation-Ready Chemistry Laboratory
                  Instruments with Executable Behaviour Contracts},
  author       = {Song, Zirui and Zhang, Jiadong and Zhang, Jiahao and
                  Ma, Xiaosong and Chang, Xiaojun and Chen, Xiuying},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/datasets/MBZUAI/ChemLab-Assets}},
  note         = {Dataset, release 1.1}
}
```
