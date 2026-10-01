# Extra gate evidence

## FVG+BOS examples (smoke M15, ObMode=1)

| setup | dir | swing peak bar | swing confirmed (bar closed) | swing level | break close bar | OB candle | activation |
|---|---|---|---|---|---|---|---|
| 189 | L | 2025-11-28 20:30 | 2025-11-28 21:15 (closed 2025-11-28 21:30) | 4226.63 | 2025-12-01 02:15 | 2025-12-01 01:45 | 2025-12-01 02:30 |
| 200 | S | 2025-12-01 19:00 | 2025-12-01 19:45 (closed 2025-12-01 20:00) | 4223.40 | 2025-12-02 03:00 | 2025-12-02 02:30 | 2025-12-02 03:15 |


## Trades from MT5 deal records: FVG+BOS examples

| setup | dir | order price (intended entry) | MT5 fill price | SL | TP | MT5 exit price | exit | lots | planned risk USD | gross USD | swap USD | commission USD | net USD | R net | R before costs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 189 | L | 4234.99 | 4234.99 | 4221.83 | 4261.31 | 4221.83 | sl | 0.07 | 92.12 | -92.12 | +0.00 | +0.00 | -92.12 | -1.00 | -1.00 |
| 200 | S | 4221.77 | 4221.78 | 4228.69 | 4207.93 | 4207.92 | tp | 0.14 | 96.88 | +194.04 | +0.00 | +0.00 | +194.04 | +2.00 | +2.00 |


## Trades from MT5 deal records: gate_entry_ob_edge_m15

| setup | dir | order price (intended entry) | MT5 fill price | SL | TP | MT5 exit price | exit | lots | planned risk USD | gross USD | swap USD | commission USD | net USD | R net | R before costs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 383 | S | 5127.12 | 5127.15 | 5138.27 | 5104.82 | 5138.67 | sl | 0.08 | 89.20 | -92.16 | +0.00 | +0.00 | -92.16 | -1.03 | -1.00 |
| 386 | L | 5164.42 | 5164.33 | 5146.68 | 5199.90 | 5146.55 | sl | 0.05 | 88.70 | -88.90 | +0.00 | +0.00 | -88.90 | -1.00 | -1.00 |


## Trades from MT5 deal records: gate_entry_ob_mid_m15

| setup | dir | order price (intended entry) | MT5 fill price | SL | TP | MT5 exit price | exit | lots | planned risk USD | gross USD | swap USD | commission USD | net USD | R net | R before costs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 383 | S | 5132.65 | 5132.85 | 5138.27 | 5121.41 | 5138.67 | sl | 0.17 | 95.54 | -98.94 | +0.00 | +0.00 | -98.94 | -1.04 | -1.00 |
| 386 | L | 5155.60 | 5155.36 | 5146.68 | 5173.44 | 5146.55 | sl | 0.11 | 98.12 | -96.91 | +0.00 | +0.00 | -96.91 | -0.99 | -1.00 |
