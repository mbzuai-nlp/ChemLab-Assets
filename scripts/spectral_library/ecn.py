"""Effective carbon number (ECN) for flame-ionisation-detector response, from a SMILES string.

Rules after Scanlon & Willis, J. Chromatogr. Sci. 23 (1985) 333 (aliphatic/aromatic C 1.0, olefinic 0.95,
acetylenic 1.30, carbonyl C 0, nitrile C 0.3, ether O -1.0, primary/secondary/tertiary alcohol O
-0.6/-0.75/-0.25, ester single-bonded O -0.25, carboxylic-acid OH -0.6 treated like a primary alcohol, amine N
-0.6/-0.75/-0.25 by substitution, halogen on aliphatic C -0.12, on olefinic C +0.05).  Atoms with no rule (S, P,
nitro N, ...) contribute 0.  The result is an approximation of published response factors, so every consumer
tags it ``computed_ecn_scanlon_willis_1985``.
"""
from __future__ import annotations

from rdkit import Chem


def effective_carbon_number(smiles: str) -> dict:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"bad SMILES {smiles!r}")
    total = 0.0
    detail: dict[str, float] = {}

    def add(label: str, v: float):
        nonlocal total
        total += v
        detail[label] = detail.get(label, 0.0) + v

    for atom in mol.GetAtoms():
        z = atom.GetAtomicNum()
        if z == 6:
            nbrs = atom.GetNeighbors()
            dbl_o = any(b.GetBondType() == Chem.BondType.DOUBLE and b.GetOtherAtom(atom).GetAtomicNum() == 8 for b in atom.GetBonds())
            triple_n = any(b.GetBondType() == Chem.BondType.TRIPLE and b.GetOtherAtom(atom).GetAtomicNum() == 7 for b in atom.GetBonds())
            if dbl_o:
                add("carbonyl_C", 0.0)
            elif triple_n:
                add("nitrile_C", 0.3)
            elif atom.GetIsAromatic():
                add("aromatic_C", 1.0)
            elif any(b.GetBondType() == Chem.BondType.TRIPLE for b in atom.GetBonds()):
                add("acetylenic_C", 1.30)
            elif any(b.GetBondType() == Chem.BondType.DOUBLE and b.GetOtherAtom(atom).GetAtomicNum() == 6 for b in atom.GetBonds()):
                add("olefinic_C", 0.95)
            else:
                add("aliphatic_C", 1.0)
        elif z == 8:
            if any(b.GetBondType() == Chem.BondType.DOUBLE for b in atom.GetBonds()):
                continue  # carbonyl oxygen: handled through the carbon rule
            heavy = [n for n in atom.GetNeighbors() if n.GetAtomicNum() > 1]
            if atom.GetTotalNumHs() == 1 and len(heavy) == 1:
                c = heavy[0]
                acid = any(b.GetBondType() == Chem.BondType.DOUBLE and b.GetOtherAtom(c).GetAtomicNum() == 8 for b in c.GetBonds())
                if acid:
                    add("acid_OH", -0.6)
                elif c.GetIsAromatic():
                    add("phenol_OH", -0.6)
                else:
                    subs = sum(1 for n in c.GetNeighbors() if n.GetAtomicNum() == 6)
                    add({1: "primary_OH", 2: "secondary_OH"}.get(subs, "tertiary_OH"), {1: -0.6, 2: -0.75}.get(subs, -0.25))
            elif len(heavy) == 2:
                ester = any(any(b.GetBondType() == Chem.BondType.DOUBLE and b.GetOtherAtom(c).GetAtomicNum() == 8 for b in c.GetBonds()) for c in heavy)
                add("ester_O" if ester else "ether_O", -0.25 if ester else -1.0)
            else:
                add("other_O", 0.0)
        elif z == 7:
            if any(n.GetAtomicNum() == 8 for n in atom.GetNeighbors()):
                add("nitro_N", 0.0)
            elif any(b.GetBondType() == Chem.BondType.TRIPLE for b in atom.GetBonds()):
                continue  # nitrile N counted with its carbon
            elif atom.GetIsAromatic():
                add("aromatic_N", -0.6)
            elif any(b.GetBondType() == Chem.BondType.DOUBLE for b in atom.GetBonds()):
                add("imine_N", -0.6)
            else:
                heavy = sum(1 for n in atom.GetNeighbors() if n.GetAtomicNum() > 1)
                amide = any(any(b.GetBondType() == Chem.BondType.DOUBLE and b.GetOtherAtom(n).GetAtomicNum() == 8 for b in n.GetBonds()) for n in atom.GetNeighbors() if n.GetAtomicNum() == 6)
                if amide:
                    add("amide_N", -0.6)
                else:
                    add({1: "primary_N", 2: "secondary_N"}.get(heavy, "tertiary_N"), {1: -0.6, 2: -0.75}.get(heavy, -0.25))
        elif z in (9, 17, 35, 53):
            c = next((n for n in atom.GetNeighbors() if n.GetAtomicNum() == 6), None)
            olefinic = c is not None and (c.GetIsAromatic() or any(b.GetBondType() == Chem.BondType.DOUBLE for b in c.GetBonds()))
            add("halogen_on_olefinic_C" if olefinic else "halogen_on_aliphatic_C", 0.05 if olefinic else -0.12)
        else:
            add(f"no_rule_{atom.GetSymbol()}", 0.0)
    n_carbon = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 6)
    return {"ecn": round(max(total, 0.0), 3), "n_carbon": n_carbon, "contributions": {k: round(v, 3) for k, v in detail.items()}}


if __name__ == "__main__":
    for s in ("CCCCCCC", "CCO", "CC(=O)O", "CCOC(=O)C", "c1ccccc1", "CC(C)=O", "ClC(Cl)Cl", "O"):
        print(s, effective_carbon_number(s))
