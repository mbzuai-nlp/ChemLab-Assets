"""Write assets/reactions/spectral_library/说明.md from index.json (coverage table + provenance notes)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "assets" / "reactions" / "spectral_library"

idx = json.load(open(LIB / "index.json", encoding="utf-8"))
comp = json.load(open(LIB / "compounds.json", encoding="utf-8"))["compounds"]
rows = idx["compounds"]
n = len(rows)


def count(key):
    return sum(1 for v in rows.values() if (v[key] if isinstance(v[key], bool) else bool(v[key])))


cov = [
    ("气相色谱 GC7890B / GC8890B / HS8697", "NIST 保留指数（非极性 / 极性柱）", count("gc_retention_index_classes"), "gc_retention.json → retention_index"),
    ("气相色谱 GC7890B / GC8890B", "可直接喂给模型的指纹 k_ref / ΔH / 响应", count("gc_model_fingerprint"), "gc_retention.json → model_fingerprint"),
    ("红外 FTIR-4700", "NIST 红外谱（400–4000 cm⁻¹ 吸光度网格 + 峰表）", count("ir_spectra"), "ir_spectra.json"),
    ("GC-MS / 未来质谱", "NIST/EPA/NIH 电子轰击质谱峰表", count("ms"), "ms_ei.json"),
    ("紫外可见 DR6000", "NIST 紫外谱曲线", count("uv_spectra"), "uv_vis.json → spectra"),
    ("紫外可见 DR6000", "λmax 文献记录（PubChem）", count("uv_lambda_max_records"), "uv_vis.json → lambda_max_records"),
    ("核磁 Spinsolve 80", "¹H 峰表（HMDB，经 PubChem）", count("nmr_1h_spectra"), "nmr.json → 1H"),
    ("核磁 Spinsolve 80", "¹³C 峰表（HMDB，经 PubChem）", count("nmr_13c_spectra"), "nmr.json → 13C"),
    ("液相 HPLC-1260", "反相 C18 模板（XLogP 推算保留 + 检测波长）", count("hplc_template"), "hplc_uv.json"),
]
lines = [
    "# 谱图模板库（spectral_library）",
    "",
    f"共 {n} 种常见实验室化合物（清单见 `scripts/spectral_library/compounds_list.py`），每种化合物按仪器类型整理成可直接读取的 JSON 模板。",
    "所有数值都带 `evidence` 与 `source` 字段：`measured` / `measured_literature` 是数据库里的实测或文献值，`derived` 是由实测值经公开公式换算，`*_assumption` 是我们自己的标定假设。",
    "",
    "## 覆盖情况",
    "",
    "| 对应资产 | 模板内容 | 有数据的化合物数 | 文件 |",
    "|---|---|---:|---|",
]
lines += [f"| {a} | {b} | {c} / {n} | `{d}` |" for a, b, c, d in cov]
lines += [
    "",
    "## 数据来源与许可",
    "",
    "- **NIST Chemistry WebBook**（https://webbook.nist.gov）：保留指数表、红外 / 紫外 JCAMP-DX 谱、质谱峰表、沸点与汽化焓。NIST 标准参考数据条款：可自由查询与用于研究，批量再分发需注明来源。",
    "- **PubChem**（https://pubchem.ncbi.nlm.nih.gov）：CID、分子式、SMILES、XLogP；¹H/¹³C 峰表来自 HMDB（经 PubChem 转载，HMDB 对学术使用免费）。",
    "- 原始下载缓存在 `references/spectral_raw/<id>/`，可离线重建。",
    "",
    "## 由实测值到模型参数的换算（GC）",
    "",
    "- 保留指数 I 按柱类分组取中位数（先剔除与中位数相差 > 60 的离群值；优先毛细管柱）。柱类：`standard_nonpolar`（100 % 甲基硅氧烷）、`semi_standard_nonpolar`（5 % 苯基）、`standard_polar`（聚乙二醇 / WAX）。",
    "- 模型柱（30 m × 0.25 mm × 0.25 µm，1 mL/min，80 °C 参考温度）的 k_ref 由正构烷烃标定得到：log₁₀ k = log₁₀ k(己烷) + s·(I/100 − 6)。标定常数（非极性 k(己烷)=0.45, s=0.332；极性 0.10, 0.28）是**假设**，写在 `gc_retention.json → gc_model.calibration`。",
    "- ΔH 取 −ΔvapH（NIST 汽化焓，优先 300–480 K 区间内最接近 373 K 的值；缺失时用 Trouton 规则 88 J/(mol·K) × 沸点）。",
    "- FID 响应 response_C_kg = 15 C/kg(C) × 12.011 × ECN / M，ECN 用 Scanlon & Willis (1985) 规则从 SMILES 计算（`ecn.py`）。",
    "- `retention_index` 里的 I 是实测数据；`model_fingerprint` 是换算结果，evidence=derived。",
    "",
    "## 其它仪器模板说明",
    "",
    "- 红外：每种化合物保留最多 4 条 NIST 谱（气相 / 液膜 / 溶液），统一重采样到 400–4000 cm⁻¹、步长 4 的归一化吸光度网格，附前 15 个峰。液相 / 溶液谱排在前面，适合 FTIR-4700 的 ATR / 液池场景。",
    "- 红外解码（2026-10-14 修复，`jcamp.py` + `build_library.py`）：DELTAX 与 FIRSTX/LASTX/NPOINTS 矛盾（符号相反或差 > 1 %）时改用 (LASTX−FIRSTX)/(NPOINTS−1)；多数据块文件逐块解析——"
    "光学常数文件取虚部（吸收指数 k）块并乘以波数（α = 4πkν̃ 的形状），积分球反射率文件取第一块（总反射率）按 log10(1/R) 当伪吸光度（不是 Kubelka–Munk 校正），"
    "只有实部折射率的光学常数文件不含吸收信息，不再当吸光度存储，列在 `ir_spectra.json → excluded_spectra`。被纠正的记录带 `y_transform` 或 `x_step_note` 字段。"
    f"修复前后的逐条对比与证明见 `outputs/spectral_library_expansion/rebuild_diff.json`（`scripts/spectral_library/verify_rebuild.py`）。原 FT/IR-4700 纠错覆盖层已退役，FT/IR-4700 直接读本库。",
    "- 质谱：NIST 电子轰击（70 eV）峰表，m/z 与相对强度（基峰 = 100）。",
    "- 紫外可见：NIST 曲线（多数为气相，波长 nm 对 log ε）以及 PubChem 文献 λmax；DR6000 的比色方法（Hach 方法号）不在本库范围。",
    "- 核磁：HMDB 实测峰表（含仪器频率、溶剂、pH），并按 0.08 ppm（¹H）/ 0.6 ppm（¹³C）间距聚成多重峰质心与积分，便于在 80 MHz 台式机上按谱线位置重新合成。",
    "- 液相：反相保留与方法强相关，只提供 XLogP 推算的洗脱顺序估计（evidence=derived_assumption）和可用的检测波长候选。",
    "",
    "## 扩充与共享 id 交叉索引（2026-10-14）",
    "",
    "- 扩充选择：扫描全部仪器模板库（`assets/reactions/*_library/**/*.json` 及各合同 `chemistry_lab.output_templates` 引用的库文件）里的 CAS，"
    "按出现在多少台仪器的库中排序；必选 substances.json 中尚未入库的分子物种（反应模板用）与 ≥ 2 台仪器库中出现的常用溶剂/试剂，其余按排名补足，"
    "且须 NIST WebBook 有 GC 保留指数 / 红外 / 质谱 / 紫外中的至少一种。选择依据与排名见 `outputs/spectral_library_expansion/selection.json`"
    "（`scripts/spectral_library/select_expansion.py`）。原 95 个 id 不变。",
    "- 交叉索引 `assets/reactions/shared_id_crosswalk.json`（`scripts/spectral_library/build_crosswalk.py`）：把每个带 CAS / InChIKey / PubChem CID 的库记录映射到本库 id 与 substances.json id，"
    "含逐库覆盖统计和“化合物 → 有真实模板的仪器与记录”反查。代码里用 `scripts/template_library.py` 的 `crosswalk()`、`shared_id_for_record(库路径, 记录 id)`、`libraries_for_compound(化合物 id)`。",
    "- `apply_to_substances.py` 只给缺少 `gc_7890b_fid` 的物种补指纹，从不改动已有值。",
    "",
    "## 重建",
    "",
    "```bash",
    "conda activate matterix",
    "python scripts/spectral_library/select_expansion.py     # （扩充时）排名 + 探测 + 选择 → outputs/spectral_library_expansion/selection.json",
    "python scripts/spectral_library/fetch_sources.py        # 下载（已缓存的文件跳过；--ids 只抓新化合物，--skip-unlisted 跳过 NIST 未列出的数据类型）",
    "python scripts/spectral_library/build_library.py        # 解析 → JSON",
    "python scripts/spectral_library/write_readme.py         # 本文件",
    "python scripts/spectral_library/apply_to_substances.py  # 给缺指纹的物种补 derived 指纹（只增不改）",
    "python scripts/spectral_library/gc_demo_real.py         # 用真实指纹跑 GC 运行时，出色谱图与峰表",
    "python scripts/spectral_library/build_crosswalk.py      # 共享 id 交叉索引 → assets/reactions/shared_id_crosswalk.json",
    "```",
    "",
    "## 化合物清单",
    "",
    "| id | 中文名 | CAS | GC 指纹 | IR | MS | UV | ¹H | ¹³C |",
    "|---|---|---|:-:|:-:|:-:|:-:|:-:|:-:|",
]
for sid, v in rows.items():
    c = comp[sid]
    yn = lambda x: "✓" if x else "–"
    lines.append(f"| {sid} | {c['name_zh']} | {c['cas']} | {yn(v['gc_model_fingerprint'])} | {yn(v['ir_spectra'])} | {yn(v['ms'])} | {yn(v['uv_spectra'] or v['uv_lambda_max_records'])} | {yn(v['nmr_1h_spectra'])} | {yn(v['nmr_13c_spectra'])} |")
(LIB / "说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("wrote", LIB / "说明.md", f"({n} compounds)")
