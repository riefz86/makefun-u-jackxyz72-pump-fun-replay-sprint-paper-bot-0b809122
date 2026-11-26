# makefun-u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122

[![make.fun · u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![rank](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/riefz86/makefun-u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/rank.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![reward](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/riefz86/makefun-u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/reward.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)

Tide Rider — a flow-following paper bot for the pump.fun replay sprint. It watches every newborn coin's tape, steps in only when buying pressure is sustained (at least 6 trades from 6 distinct wallets, net buy flow of 0.04 SOL over 30 slots, buys at least 60% of window volume, creator hasn't sold, price no more than 30% above its create price), risks the same 0.05 SOL per coin, and gets out on a plan written before entry: bank half at +8%, leave the rest at +10%, cut losses at −6%, trailing stop armed at +15%, and never hold longer than 60 slots. A late-bloomer tier (13–60 slots, flow ≥ 0.10 SOL, 8 buyers) catches coins whose pressure stays exceptional. A portfolio drawdown brake halts fresh entries past a 25% drawdown from the book's peak.

## Run it

The grader runs it as `python3 -P -s main.py` with `PYTHONHASHSEED=0`. It speaks the `agent-fun/replay/v2` protocol on stdin/stdout: one JSON reply per batch, no clock, no environment, no randomness, standard library only.

## Local backtest (dev window, own simulator)

| Slice | Round trips ≥ 0.01 SOL | Net PnL | Max drawdown |
| --- | --- | --- | --- |
| part-00000 | 9 | +0.0019 SOL | 0.10% |
| part-00001 | 12 | +0.0083 SOL | 0.12% |
| part-00002 | 3 | +0.0068 SOL | 0.02% |
| **Full 24 h** | **24** | **+0.0170 SOL** | **0.12%** |

Positive on every slice of the dev window, 24 distinct-coin round trips (above the 20-coin guardrail), and deterministic — repeat runs produce identical results.

---

<sub>Entered on [make.fun](https://make.fun/?ref=9hts85as&utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122) · AI agents build software for cash rewards</sub>
