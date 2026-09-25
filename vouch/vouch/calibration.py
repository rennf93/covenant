"""Calibration: turn laya's raw outputs into a probability of an up move.

Fits a tiny logistic regression (pure python, no deps) on logged decisions:
features = [conviction, enter_p, long-short spread], label = did price go up
over the next H decisions. Saved to out/calibration.json; System-1 loads it
and, when rules.min_edge_pct > 0, gates entries on expected value over
round-trip costs instead of hand-tuned probability cutoffs.

Layering: leaf (pure python, no package imports). Imported by BOTH engine
(System-1's entry gate) and analysis (the fit), so it must stay dependency-free.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

FEATURES = ["conviction", "enter_p", "ls_spread"]


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


class Calibrator:
    def __init__(self, weights: dict[str, float], bias: float, meta: dict | None = None):
        self.weights = weights
        self.bias = bias
        self.meta = meta or {}

    def p_up(self, conviction: float, enter_p: float, ls_spread: float) -> float:
        z = self.bias
        z += self.weights.get("conviction", 0.0) * conviction
        z += self.weights.get("enter_p", 0.0) * enter_p
        z += self.weights.get("ls_spread", 0.0) * ls_spread
        return _sigmoid(z)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"weights": self.weights, "bias": self.bias, **self.meta}, indent=2)
        )

    @classmethod
    def load(cls, path: Path) -> Calibrator | None:
        try:
            d = json.loads(Path(path).read_text())
            return cls(
                d["weights"],
                d["bias"],
                {k: v for k, v in d.items() if k not in ("weights", "bias")},
            )
        except Exception:  # noqa: BLE001 - no calibration is a normal state
            return None

    @classmethod
    def fit(
        cls,
        rows: list[dict],
        horizon: int = 15,
        l2: float = 0.01,
        lr: float = 0.1,
        epochs: int = 300,
    ) -> Calibrator | None:
        """rows: dicts with conviction/enter_p/ls_spread and 'fwd_ret' precomputed.
        Label: fwd_ret > 0. Returns None if there is nothing to learn from."""
        for r in rows:
            if r.get("fwd_ret") is not None and "y" not in r:
                r["y"] = 1.0 if r["fwd_ret"] > 0 else 0.0
        return cls.fit_labeled(rows, l2=l2, lr=lr, epochs=epochs, meta={"horizon": horizon})

    @classmethod
    def fit_labeled(
        cls,
        rows: list[dict],
        l2: float = 0.01,
        lr: float = 0.1,
        epochs: int = 300,
        meta: dict | None = None,
    ) -> Calibrator | None:
        """rows carry conviction/enter_p/ls_spread and an explicit 'y' in {0,1}.

        Used by run_calibration.py to fit on barrier labels (y = up-barrier
        first) over tradeable rows only, where 'fwd_ret > 0' would be the
        wrong question: the EV gate spends P(up-barrier-first), not
        P(sign of the horizon return)."""
        X, y = [], []
        for r in rows:
            if r.get("y") is None:
                continue
            X.append([r.get("conviction", 0.0), r.get("enter_p", 0.0), r.get("ls_spread", 0.0)])
            y.append(float(r["y"]))
        n_pos = sum(y)
        if len(y) < 100 or n_pos < 20 or n_pos > len(y) - 20:
            return None  # not enough data, or no class balance to learn from

        # standardize features; keep means/scales so inference matches
        n_f = len(FEATURES)
        means = [sum(col) / len(col) for col in zip(*X, strict=False)]
        scales = []
        for j in range(n_f):
            var = sum((x[j] - means[j]) ** 2 for x in X) / len(X)
            scales.append(math.sqrt(var) or 1.0)
        Z = [[(x[j] - means[j]) / scales[j] for j in range(n_f)] for x in X]

        w = [0.0] * n_f
        b = 0.0
        n = len(Z)
        for _ in range(epochs):
            gw = [0.0] * n_f
            gb = 0.0
            for z_i, yi in zip(Z, y, strict=False):
                p = _sigmoid(sum(wj * zj for wj, zj in zip(w, z_i, strict=False)) + b)
                err = p - yi
                for j in range(n_f):
                    gw[j] += err * z_i[j]
                gb += err
            for j in range(n_f):
                w[j] -= lr * (gw[j] / n + l2 * w[j])
            b -= lr * gb / n

        weights = {name: w[j] for j, name in enumerate(FEATURES)}
        # fold standardization back into a single linear map for inference
        fold_w = {FEATURES[j]: weights[FEATURES[j]] / scales[j] for j in range(n_f)}
        std_bias = b - sum(fold_w[FEATURES[j]] * means[j] for j in range(n_f))
        out_meta = {
            "n_samples": n,
            "n_pos": int(n_pos),
            "feature_order": FEATURES,
        }
        if meta:
            out_meta.update(meta)
        return cls(fold_w, std_bias, out_meta)
