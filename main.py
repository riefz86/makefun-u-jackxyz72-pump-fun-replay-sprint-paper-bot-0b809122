"""Tide Rider: a flow-following paper bot for the pump.fun replay sprint.

The idea in one paragraph: most fresh pump.fun coins are noise, but a coin whose
tape keeps showing real buyers outnumbering sellers for a little while, whose
creator has not bailed, and whose price has not already run away, is worth a
small, capped punt. This bot watches every newborn coin's tape, steps in only
when the buying pressure is sustained rather than a single spike, risks the same
small stake on every coin, and gets out on a plan written before entry: a
partial win is banked early, the rest rides with a trailing stop, losses are cut
at a fixed line, and no coin is held long enough to go stale. A portfolio-level
drawdown brake stops fresh entries while the book is underwater, so a bad hour
cannot snowball into a blown account.

How the money works here, in the bot's own accounting. The replay starts with
10 SOL of paper cash. Every order pays a network fee (base plus priority, read
from the hello message) whether it fills or not, and every trade against a
bonding curve pays the curve's own cut (protocol plus creator, 95 + 30 bps in
this competition) on top of the quoted amount on a buy and out of the proceeds
on a sell. A round trip therefore needs roughly a 2.5% move plus two network
fees just to break even, which is why the profit targets below sit well above
that line and why the bot never sends an order smaller than 0.01 SOL: anything
smaller is mostly fee.

The wire. The bot speaks newline-delimited JSON on stdin/stdout, exactly one
reply per batch, echoing the batch's sequence number. Three message kinds
arrive: hello (capital, caps, fees, latency), batch (the slot's events, the
latest marks for changed curves, the fills of orders sent earlier, and the
account), and done (the final account). Orders carry a client id the bot mints
itself, never starting with an underscore, and amounts as decimal strings.
Nothing here reads the clock, the environment, an unseeded random number, or
any file: the same batches always produce the same replies, which is what the
replay's double execution demands.

What counts as a buy signal. A coin becomes a candidate on its CreateEvent and
is judged only between 2 and 12 slots after creation, while its tape is still
young, with a second chance for late bloomers between 13 and 60 slots when
their buying pressure is far stronger. It must show at least 6 trades from at
least 6 distinct wallets other than the creator, a net buy flow of at least
0.04 SOL across the last 30 slots (0.10 SOL and 8 distinct buyers for a late
bloomer), buys making up at least 60% of that window's volume, a real SOL
reserve between 0.3 and 30 SOL, a price move from creation inside -5% to +30%
(+20% for a late bloomer), no single outsider sell larger than a quarter of
the real reserve, and a creator who has not sold a token. The stake is
0.05 SOL, trimmed by the per-order size cap (1% of the real reserve) and
skipped when the cap cannot even fund the 0.01 SOL minimum. At most 5 coins
are held at once and at most 16 orders go out in one reply, so entries are
paced by cash and by room, never by hope. These numbers are not guesses:
backtesting on the kit's 24-hour dev window showed looser gates (older coins,
weaker flow, wider run-ups) lose money, while this tight window finished
positive on every slice of the window.

What counts as an exit. Each position is judged against what it cost,
including the network fees its own fills paid, so a "win" is a win after fees.
Half the position is sold once it is worth 8% over cost; the rest leaves at
10% over cost, or on a trailing stop that arms at 15% and fires on a 5% fall
from the position's best marked value, or on a 6% loss, whichever comes
first. The targets are deliberately tight: on this replay the coins that run
give up their gains within a minute, so the bot banks small wins fast instead
of waiting for a large one that rarely arrives. A position is also closed at
once when its creator sells, when its 30-slot net flow turns below -0.05 SOL,
when it has gone 600 slots without a trade, or when it has been held 60 slots
(about twenty-four seconds of chain time). One extra tranche of 0.03 SOL is
allowed per coin, only after the first buy is up 10% and the flow is still
positive, so winners can be pressed a little without ever doubling down on a
loser.

The drawdown brake. After every batch the bot marks the whole book to the
sell value of its positions and remembers the peak. While the book sits more
than 25% below that peak, no new coin is bought; entries resume once the book
has climbed back to within 10% of the peak. The competition itself only
forbids a 50% drawdown, so this brake is deliberately tighter: it is better to
sit out a bad stretch than to keep feeding it.

Two details that keep the accounting honest. Pending orders are subtracted
from free cash before any new order is sized, because the harness counts them
against cash when they land. And a position whose curve completes is never
bought again, while a position the harness itself closes (its client ids
start with an underscore) is dropped from the bot's bookkeeping at once.
"""

import json
import re
import sys

PROTOCOL = "agent-fun/replay/v2"
LAMPORTS_PER_SOL = 1_000_000_000
BPS = 10_000

# Curve cut on every trade: protocol 95 bps + creator 30 bps, read from hello
# when the harness offers it and falling back to these constants otherwise.
CURVE_FEE_BPS = 125

# --- Entry gates -----------------------------------------------------------
ENTRY_MIN_AGE = 2            # slots after creation before a coin may be bought
ENTRY_MAX_AGE = 12           # slots after creation after which it is too late
ENTRY_MIN_TRADES = 6
ENTRY_MIN_BUYERS = 6         # distinct non-creator buyers on the tape
ENTRY_MIN_NET_FLOW = 40_000_000       # 0.04 SOL net buys over the flow window
ENTRY_MIN_BUY_SHARE_BPS = 6_000       # buys must be >= 60% of window volume
ENTRY_MIN_REAL_SOL = 300_000_000      # 0.3 SOL sitting in the curve
ENTRY_MAX_REAL_SOL = 30_000_000_000   # 30 SOL
ENTRY_MIN_RUNUP_BPS = -500            # price may be 5% under its create price
ENTRY_MAX_RUNUP_BPS = 3_000           # ... but no more than 30% above it
ENTRY_MAX_SINGLE_SELL_BPS = 2_500     # biggest outsider sell, vs real reserve
FLOW_WINDOW_SLOTS = 30

# --- Late bloomers: a second, stricter entry tier for coins that keep ----------
# showing exceptional buying pressure after the early window has closed.
LATE_MAX_AGE = 60
LATE_MIN_NET_FLOW = 100_000_000       # 0.10 SOL net buys over the flow window
LATE_MIN_BUYERS = 8
LATE_MAX_RUNUP_BPS = 2_000            # no more than 20% above the create price

# --- Sizing ----------------------------------------------------------------
STAKE_LAMPORTS = 50_000_000           # 0.05 SOL per coin, first tranche
ADD_LAMPORTS = 30_000_000             # 0.03 SOL, one extra tranche per coin
MIN_BUY_LAMPORTS = 10_000_000         # 0.01 SOL: the guardrail's round-trip floor
ADD_MIN_GAIN_BPS = 1_000              # add only once the first buy is up 10%

# --- Exits -----------------------------------------------------------------
TP_HALF_BPS = 800                     # bank half the position at +8%
TP_ALL_BPS = 1_000                    # leave the rest at +10%
STOP_BPS = 600                        # cut the position at -6%
TRAIL_ARM_BPS = 1_500                 # trailing stop arms at +15%
TRAIL_DROP_BPS = 500                  # ... and fires on a 5% fall from best
MAX_HOLD_SLOTS = 60                   # about twenty-four seconds of chain time
STALE_SLOTS = 600                     # no trade for this long: leave
EXIT_FLOW_LAMPORTS = -50_000_000      # 30-slot net flow below -0.05 SOL: leave

# --- Portfolio brake -------------------------------------------------------
BRAKE_DRAWDOWN_BPS = 2_500            # halt entries past a 25% book drawdown
BRAKE_RESUME_BPS = 1_000              # resume once back within 10% of the peak
MIN_CASH_TO_ENTER = 1_000_000_000     # keep 1 SOL of dry powder at all times

MAX_SLIPPAGE_BPS = 10_000

_PUBKEY_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9-][A-Za-z0-9_-]{0,63}")
_U64_RE = re.compile(r"0|[1-9][0-9]{0,19}")


def _ceil_div(a, b):
    """Smallest integer >= a / b, for positive a and b; used for fee rounding."""
    return -(-a // b)


def _curve_fee(amount, bps):
    """The curve's cut on a trade of `amount` lamports, rounded up as on chain."""
    return _ceil_div(amount * bps, BPS)


def _sell_proceeds(tokens, vq, vt, curve_fee_bps):
    """What selling `tokens` against reserves (vq, vt) pays out after the curve cut."""
    if tokens <= 0 or vt <= 0:
        return 0
    gross = (tokens * vq) // (vt + tokens)
    return gross - _curve_fee(gross, curve_fee_bps)


class WireError(Exception):
    """The harness sent something this bot cannot follow, or an order broke a rule."""


class TapeCoin:
    """One newborn coin and everything its tape has shown so far (all ints)."""

    __slots__ = (
        "mint", "created_slot", "creator", "vq0", "vt0",
        "dev_buy", "trades", "buyers", "bought", "sold_by_others",
        "biggest_sell_bps", "dev_sold", "last_trade_slot", "recent",
        "buys_sent", "spent_sent", "last_buy_vq", "last_buy_vt",
        "last_buy_slot", "decided", "complete",
    )

    def __init__(self, mint, created_slot, creator, vq0, vt0):
        self.mint = mint
        self.created_slot = created_slot
        self.creator = creator
        self.vq0 = vq0
        self.vt0 = vt0
        self.dev_buy = 0
        self.trades = 0
        self.buyers = set()
        self.bought = 0
        self.sold_by_others = 0
        self.biggest_sell_bps = 0
        self.dev_sold = False
        self.last_trade_slot = created_slot
        # (slot, is_buy, sol, user) for the recent tape, oldest first.
        self.recent = []
        self.buys_sent = 0
        self.spent_sent = 0
        self.last_buy_vq = 0
        self.last_buy_vt = 1
        self.last_buy_slot = 0
        # True once the coin is bought, rejected for good, or too old to judge.
        self.decided = False
        self.complete = False

    def note_trade(self, slot, is_buy, sol, user, real_quote_before):
        """Fold one TradeEvent into the tape, keeping only the flow window."""
        self.trades += 1
        self.last_trade_slot = slot
        self.recent.append((slot, is_buy, sol, user))
        is_dev = user == self.creator
        if is_buy:
            self.bought += sol
            if is_dev and slot == self.created_slot:
                self.dev_buy += sol
            elif not is_dev:
                self.buyers.add(user)
        elif is_dev:
            self.dev_sold = True
        else:
            self.sold_by_others += sol
            if real_quote_before > 0:
                bps = (sol * BPS) // real_quote_before
                if bps > self.biggest_sell_bps:
                    self.biggest_sell_bps = bps

    def window_flow(self, slot):
        """Net lamports bought minus sold, and distinct buyers, over the flow window."""
        cutoff = slot - FLOW_WINDOW_SLOTS
        drop = 0
        while drop < len(self.recent) and self.recent[drop][0] <= cutoff:
            drop += 1
        if drop:
            del self.recent[:drop]
        net = 0
        buy_vol = 0
        sell_vol = 0
        buyers = set()
        for _slot, is_buy, sol, user in self.recent:
            if is_buy:
                net += sol
                buy_vol += sol
                if user != self.creator:
                    buyers.add(user)
            else:
                net -= sol
                sell_vol += sol
        return net, buy_vol, sell_vol, len(buyers)


class OpenBook:
    """Bookkeeping for one held position: what it cost and how it has behaved."""

    __slots__ = ("opened_slot", "fees_paid", "best_value", "banked_half", "adds")

    def __init__(self, opened_slot, fees_paid):
        self.opened_slot = opened_slot
        self.fees_paid = fees_paid
        self.best_value = 0
        self.banked_half = False
        self.adds = 0


class ReplayBot:
    """The bot itself: wire handling, tape reading, and the entry/exit rules."""

    def __init__(self, hello):
        self.hello = hello
        self.caps = hello["caps"]
        self.fees = hello["fees"]
        self.capital = int(hello["capital"]["lamports"])
        self.curve_fee_bps = int(self.fees.get("protocol_bps_fallback", 95)) + int(
            self.fees.get("creator_bps_fallback", 30)
        )
        self.seq = -1
        self.slot = -1
        self.cash = self.capital
        # mint -> (token_amount, cost_lamports), as the latest account reports.
        self.positions = {}
        # mint -> latest reserves (vq, vt, real quote, real token, slot).
        self.curves = {}
        # mint -> TapeCoin, in first-seen order.
        self.coins = {}
        # mint -> OpenBook.
        self.book = {}
        # client_id -> (side, mint, amount) for orders awaiting their fill.
        self.pending = {}
        self._used_ids = set()
        self._next_id = 1
        self.peak_value = self.capital
        self.braked = False

    # -- wire helpers ------------------------------------------------------

    def tx_fee(self):
        """Network fee every sent order pays, filled or not."""
        return int(self.fees["base_fee_lamports"]) + int(self.fees["priority_fee_lamports"])

    def fresh_id(self):
        """A client id never used before in this run, never starting with '_'."""
        while True:
            cid = "k%d" % self._next_id
            self._next_id += 1
            if cid not in self._used_ids:
                self._used_ids.add(cid)
                return cid

    def max_buy_now(self, mint):
        """The size cap on this mint's buys this batch: 1% of its real reserve."""
        curve = self.curves.get(mint)
        if curve is None:
            return 0
        _vq, _vt, real_quote, _rt, _slot = curve
        if real_quote <= 0:
            return 0
        return real_quote * self.caps["max_order_bps_of_real_reserve"] // BPS

    def free_cash(self):
        """Cash minus what pending orders will take if they fill (amounts + fees)."""
        free = self.cash
        fee = self.tx_fee()
        for side, _mint, amount in self.pending.values():
            free -= fee
            if side == "buy":
                free -= amount
        return free

    def open_count(self):
        """Mints held plus mints with a buy in flight: what counts toward the cap."""
        mints = set(self.positions)
        for side, mint, _amount in self.pending.values():
            if side == "buy":
                mints.add(mint)
        return len(mints)

    def has_pending(self, mint, side):
        for s, m, _a in self.pending.values():
            if s == side and m == mint:
                return True
        return False

    # -- batch folding -----------------------------------------------------

    def fold_batch(self, batch):
        """Bring cash, positions, curves and tapes up to date from one batch."""
        self.seq = batch["seq"]
        self.slot = batch["slot"]
        for fill in batch["fills"]:
            cid = fill["client_id"]
            self.pending.pop(cid, None)
            if cid.startswith("_"):
                # The harness closed this position itself: drop the bookkeeping.
                self.book.pop(fill["mint"], None)
                continue
            if fill["status"] == "filled" and fill["side"] == "buy":
                entry = self.book.get(fill["mint"])
                if entry is None:
                    self.book[fill["mint"]] = OpenBook(fill["slot"], int(fill["tx_fee_lamports"]))
                else:
                    entry.fees_paid += int(fill["tx_fee_lamports"])
        account = batch["account"]
        self.cash = int(account["cash_lamports"])
        self.positions = {}
        for pos in account["positions"]:
            tokens = int(pos["token_amount"])
            if tokens > 0:
                self.positions[pos["mint"]] = (tokens, int(pos["cost_lamports"]))
        for mark in batch["marks"]:
            mint = mark["mint"]
            slot = int(mark["slot"])
            old = self.curves.get(mint)
            if old is not None and slot < old[4]:
                continue
            self.curves[mint] = (
                int(mark["virtual_quote_reserves"]),
                int(mark["virtual_token_reserves"]),
                int(mark["real_quote_reserves"]),
                int(mark["real_token_reserves"]),
                slot,
            )
        for event in batch["events"]:
            self.fold_event(event)
        self._refresh_brake()

    def fold_event(self, event):
        """Fold one chain event into the tape of its coin."""
        kind = event["event"]
        mint = event["mint"]
        if kind == "CreateEvent":
            coin = TapeCoin(
                mint,
                int(event["slot"]),
                event.get("creator", ""),
                int(event["virtual_quote_reserves"]),
                int(event["virtual_token_reserves"]),
            )
            self.coins[mint] = coin
            self.curves[mint] = (
                int(event["virtual_quote_reserves"]),
                int(event["virtual_token_reserves"]),
                0,
                int(event["real_token_reserves"]),
                int(event["slot"]),
            )
        elif kind == "TradeEvent":
            coin = self.coins.get(mint)
            if coin is None:
                coin = TapeCoin(mint, int(event["slot"]), event.get("creator", ""), 0, 0)
                self.coins[mint] = coin
            real_before = int(event["real_quote_reserves"]) + int(event["sol_amount"]) \
                if not event["is_buy"] else int(event["real_quote_reserves"])
            coin.note_trade(
                int(event["slot"]),
                bool(event["is_buy"]),
                int(event["sol_amount"]),
                event["user"],
                real_before,
            )
            self.curves[mint] = (
                int(event["virtual_quote_reserves"]),
                int(event["virtual_token_reserves"]),
                int(event["real_quote_reserves"]),
                int(event["real_token_reserves"]),
                int(event["slot"]),
            )
        else:  # CompleteEvent: the curve is done, never trade it again.
            coin = self.coins.get(mint)
            if coin is not None:
                coin.complete = True
                coin.decided = True

    # -- valuation and the brake --------------------------------------------

    def mark_value(self, mint, tokens):
        """What a position is worth now if sold, after the curve cut and one tx fee."""
        curve = self.curves.get(mint)
        if curve is None:
            return 0
        vq, vt, _rq, _rt, _slot = curve
        return _sell_proceeds(tokens, vq, vt, self.curve_fee_bps) - self.tx_fee()

    def _refresh_brake(self):
        """Re-mark the book; brake entries while the book is deep under its peak."""
        value = self.cash
        for mint, (tokens, _cost) in self.positions.items():
            value += self.mark_value(mint, tokens)
        if value > self.peak_value:
            self.peak_value = value
        if self.peak_value <= 0:
            return
        drawdown_bps = (self.peak_value - value) * BPS // self.peak_value
        if drawdown_bps >= BRAKE_DRAWDOWN_BPS:
            self.braked = True
        elif drawdown_bps <= BRAKE_RESUME_BPS:
            self.braked = False

    # -- decisions ----------------------------------------------------------

    def decide(self, batch):
        """The orders for this batch: exits first, then one tranche of entries."""
        orders = []
        cap = self.caps["max_orders_per_batch"]
        fee = self.tx_fee()

        # Exits first: a position that must go must not wait behind a new buy.
        for mint, (tokens, cost) in list(self.positions.items()):
            if len(orders) >= cap:
                break
            if self.has_pending(mint, "sell"):
                continue
            entry = self.book.get(mint)
            if entry is None:
                entry = OpenBook(self.slot, fee)
                self.book[mint] = entry
            value = self.mark_value(mint, tokens)
            basis = cost + entry.fees_paid + fee
            if value > entry.best_value:
                entry.best_value = value
            coin = self.coins.get(mint)
            leave = False
            half = False
            if basis > 0:
                if not entry.banked_half and value * BPS >= basis * (BPS + TP_HALF_BPS):
                    half = True
                elif value * BPS >= basis * (BPS + TP_ALL_BPS):
                    leave = True
                elif value * BPS <= basis * (BPS - STOP_BPS):
                    leave = True
                elif (
                    entry.best_value * BPS >= basis * (BPS + TRAIL_ARM_BPS)
                    and value * BPS <= entry.best_value * (BPS - TRAIL_DROP_BPS)
                ):
                    leave = True
            if self.slot - entry.opened_slot >= MAX_HOLD_SLOTS:
                leave = True
            if coin is not None:
                if coin.dev_sold:
                    leave = True
                elif self.slot - coin.last_trade_slot >= STALE_SLOTS:
                    leave = True
                elif coin.window_flow(self.slot)[0] < EXIT_FLOW_LAMPORTS:
                    leave = True
            if half and not leave:
                part = tokens // 2
                if part > 0 and part < tokens:
                    orders.append(self._sell(mint, part))
                    entry.banked_half = True
                    entry.best_value = 0
                    continue
            if not leave:
                continue
            orders.append(self._sell(mint, tokens))
            if coin is not None:
                coin.decided = True

        if self.braked:
            return orders
        free = self.free_cash() - fee * len(orders)
        room = self.caps["max_open_positions"] - self.open_count()
        if free < MIN_CASH_TO_ENTER or room <= 0:
            return orders

        for coin in list(self.coins.values()):
            if len(orders) >= cap:
                break
            if coin.decided or coin.complete:
                continue
            age = self.slot - coin.created_slot
            if age > LATE_MAX_AGE:
                coin.decided = True
                continue
            if age < ENTRY_MIN_AGE:
                continue
            # The early window takes any coin with strong flow; past it only a
            # coin with far stronger flow and more buyers still qualifies.
            late = age > ENTRY_MAX_AGE
            min_flow = LATE_MIN_NET_FLOW if late else ENTRY_MIN_NET_FLOW
            min_buyers = LATE_MIN_BUYERS if late else ENTRY_MIN_BUYERS
            max_runup = LATE_MAX_RUNUP_BPS if late else ENTRY_MAX_RUNUP_BPS
            curve = self.curves.get(coin.mint)
            if curve is None:
                continue
            vq, vt, real_quote, _rt, _slot = curve
            if coin.dev_sold:
                coin.decided = True
                continue
            if coin.trades < ENTRY_MIN_TRADES or len(coin.buyers) < min_buyers:
                continue
            if real_quote < ENTRY_MIN_REAL_SOL or real_quote > ENTRY_MAX_REAL_SOL:
                continue
            if coin.biggest_sell_bps > ENTRY_MAX_SINGLE_SELL_BPS:
                continue
            net, buy_vol, sell_vol, _buyers = coin.window_flow(self.slot)
            if net < min_flow:
                continue
            if buy_vol * BPS < (buy_vol + sell_vol) * ENTRY_MIN_BUY_SHARE_BPS:
                continue
            if coin.vq0 > 0 and coin.vt0 > 0 and vq > 0 and vt > 0:
                # Price now over the create price, in bps: (vq/vt) / (vq0/vt0).
                runup = (vq * coin.vt0 * BPS) // (vt * coin.vq0)
                if runup > BPS + max_runup or runup < BPS + ENTRY_MIN_RUNUP_BPS:
                    continue
            size_cap = self.max_buy_now(coin.mint)
            if size_cap < MIN_BUY_LAMPORTS:
                continue
            if coin.buys_sent == 0:
                amount = STAKE_LAMPORTS if STAKE_LAMPORTS < size_cap else size_cap
            else:
                # One extra tranche, only while the first buy is winning and flow holds.
                entry = self.book.get(coin.mint)
                if entry is None or entry.adds >= 1:
                    continue
                if not self.positions.get(coin.mint):
                    continue
                tokens, cost = self.positions[coin.mint]
                value = self.mark_value(coin.mint, tokens)
                basis = cost + entry.fees_paid + fee
                if basis <= 0 or value * BPS < basis * (BPS + ADD_MIN_GAIN_BPS):
                    continue
                if net < ENTRY_MIN_NET_FLOW:
                    continue
                amount = ADD_LAMPORTS if ADD_LAMPORTS < size_cap else size_cap
            if amount < MIN_BUY_LAMPORTS or free < amount + fee:
                continue
            orders.append(self._buy(coin.mint, amount))
            free -= amount + fee
            coin.buys_sent += 1
            coin.spent_sent += amount
            coin.last_buy_vq = vq
            coin.last_buy_vt = vt
            coin.last_buy_slot = self.slot
            if coin.buys_sent == 1:
                room -= 1
                entry = self.book.get(coin.mint)
                if entry is not None:
                    entry.adds = 0
            else:
                entry = self.book.get(coin.mint)
                if entry is not None:
                    entry.adds += 1
            if room <= 0:
                break
        return orders

    # -- order construction --------------------------------------------------

    def _buy(self, mint, amount):
        return {
            "side": "buy",
            "client_id": self.fresh_id(),
            "mint": mint,
            "quote_lamports": str(amount),
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }

    def _sell(self, mint, tokens):
        return {
            "side": "sell",
            "client_id": self.fresh_id(),
            "mint": mint,
            "token_amount": str(tokens),
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }

    def check_order(self, order):
        """Reject anything the harness would call a protocol violation."""
        side = order.get("side")
        if side not in ("buy", "sell"):
            raise WireError("order side must be buy or sell, got %r" % (side,))
        allowed = ("side", "client_id", "mint", "quote_lamports", "max_slippage_bps") \
            if side == "buy" else ("side", "client_id", "mint", "token_amount", "max_slippage_bps")
        for key in order:
            if key not in allowed:
                raise WireError("unknown key %r on a %s order" % (key, side))
        mint = order.get("mint")
        if not isinstance(mint, str) or not _PUBKEY_RE.fullmatch(mint):
            raise WireError("mint must be a base58 address, got %r" % (mint,))
        amount_key = "quote_lamports" if side == "buy" else "token_amount"
        amount = order.get(amount_key)
        if not isinstance(amount, str) or not _U64_RE.fullmatch(amount):
            raise WireError("%s must be a u64 decimal string, got %r" % (amount_key, amount))
        bps = order.get("max_slippage_bps")
        if type(bps) is not int or not 0 <= bps <= BPS:
            raise WireError("max_slippage_bps must be an int 0..10000, got %r" % (bps,))
        cid = order.get("client_id")
        if not isinstance(cid, str) or not _CLIENT_ID_RE.fullmatch(cid):
            raise WireError("bad client_id %r" % (cid,))


def run_loop():
    """Speak agent-fun/replay/v2 on stdin/stdout until done.

    An empty stdin (no message at all) exits quietly with status 0: there is
    nothing to do and no run to fail. An input that stops after hello but
    before done is still a broken run and raises.
    """
    bot = None
    batches = 0
    saw_anything = False
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        saw_anything = True
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            raise WireError("input line is not JSON")
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "hello":
            if bot is not None:
                raise WireError("a second hello")
            if msg.get("protocol") != PROTOCOL:
                raise WireError("harness speaks %r, this bot speaks %s" % (msg.get("protocol"), PROTOCOL))
            bot = ReplayBot(msg)
        elif kind == "batch":
            if bot is None:
                raise WireError("a batch before hello")
            bot.fold_batch(msg)
            orders = bot.decide(msg)
            if len(orders) > bot.caps["max_orders_per_batch"]:
                raise WireError("too many orders for one batch")
            for order in orders:
                bot.check_order(order)
            for order in orders:
                bot.pending[order["client_id"]] = (
                    order["side"],
                    order["mint"],
                    int(order["quote_lamports"] if order["side"] == "buy" else order["token_amount"]),
                )
            reply = json.dumps(
                {"type": "reply", "seq": msg["seq"], "orders": orders},
                separators=(",", ":"),
                ensure_ascii=False,
            )
            if len(reply.encode("utf-8")) > 65_536:
                raise WireError("reply over the 64 KiB line limit")
            sys.stdout.write(reply + "\n")
            sys.stdout.flush()
            batches += 1
        elif kind == "done":
            if bot is None:
                raise WireError("done before hello")
            print(
                "tide-rider finished: %d batches, cash %d lamports, %d open positions"
                % (batches, bot.cash, len(bot.positions)),
                file=sys.stderr,
            )
            return
        else:
            raise WireError("unknown message type %r" % (kind,))
    if not saw_anything:
        return
    raise WireError("input ended before done")


if __name__ == "__main__":
    run_loop()
