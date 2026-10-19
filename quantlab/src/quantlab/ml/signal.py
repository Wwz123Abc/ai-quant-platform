"""Today's target portfolio: a model trained on every outcome known at the latest close, applied to that close."""
import json

import pandas as pd

from ..config import get_settings
from ..errors import InvalidInput
from . import features as F
from . import model as M
from . import portfolio as P
from . import universe


def latest(top=None, max_weight=None, pause=0.2):
    s = get_settings()
    top, max_weight = top or s.trading_top_n, max_weight or s.trading_max_weight
    u = universe.load()
    wide, skipped = F.load_panel(u["symbols"], pause=pause)
    syms = [x for x in u["symbols"] if x in wide["close"].columns]
    close, open_, volume = wide["close"], wide["open"], wide["volume"]
    raw = F.raw_features(close[syms], volume[syms])
    X = F.feature_matrix(raw)[list(F.FEATURES)]
    cal = close.index
    day = cal[-1]
    if day not in X.index.get_level_values(0):
        raise InvalidInput("最新收盘日 %s 没有可用的特征" % day.date())
    model, card = M.fit_final(X, F.labels(open_[syms]), cal)
    Xd = X.loc[day]
    scores = pd.Series(model.predict(Xd[list(F.FEATURES)].to_numpy()), index=Xd.index).sort_values(ascending=False)
    weights = P.target_weights(scores, raw["vol63"].loc[day], top, max_weight)
    card.update({"signal_date": day.date().isoformat(), "universe_as_of": u["as_of"], "universe_size": len(syms),
                 "skipped": skipped, "top": top, "max_weight": max_weight})
    folder = s.data_dir / "models" / day.date().isoformat()
    M.save(model, card, folder)
    (folder / "weights.json").write_text(json.dumps(weights.round(6).to_dict(), indent=1), encoding="utf-8")
    return {"signal_date": day.date(), "weights": weights, "scores": scores, "prices": close.loc[day], "card": card,
            "data_date": close["QQQ"].last_valid_index().date() if "QQQ" in close else day.date()}
