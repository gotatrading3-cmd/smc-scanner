"""make_signal_meta.py - mesure (sur MT5) le spread typique, la taille de point et les decimales de chaque
instrument et ecrit signal_meta.json. Ce fichier permet au mode cloud (Yahoo, sans MT5) d'utiliser des couts realistes.
A relancer de temps en temps sur le PC : python make_signal_meta.py"""
import json
from pathlib import Path
import signal_data as sd

def main():
    sd.BACKEND = "mt5"
    if not sd.connect():
        print("MT5 indisponible"); return
    m = sd._mt5mod()
    out = {}
    for sid, u in sd.UNIVERSE.items():
        si = m.symbol_info(u["mt5"])
        df = sd.get_rates(sid, "1h", 8000)
        if si is None or df is None or len(df) < 500:
            print(f"  {sid:8s} ignore (donnees insuffisantes)"); continue
        sp = float(df["spread"].median())
        out[sid] = {"point": si.point, "digits": si.digits, "contract": si.trade_contract_size,
                    "spread_pts": round(sp, 2), "cls": u["cls"], "mt5": u["mt5"]}
        print(f"  {sid:8s} digits={si.digits} point={si.point:g}  spread median={sp:6.1f} pts = {sp*si.point:.5g} en prix")
    Path(sd.META_FILE).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("ecrit :", sd.META_FILE)
    sd.shutdown()

if __name__ == "__main__":
    main()
